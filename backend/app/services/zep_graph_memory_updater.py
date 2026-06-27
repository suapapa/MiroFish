"""
    Zep map memory update service
    Dynamically update the Agent activities in the simulation to the Zep map
"""

import os
import time
import threading
import json
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
from queue import Queue, Empty

from ..utils.graphiti_adapter import GraphitiClient as Zep

from ..config import Config
from ..utils.logger import get_logger
from ..utils.locale import get_locale, set_locale

logger = get_logger('mirofish.zep_graph_memory_updater')


@dataclass
class AgentActivity:
    """Agent activity record"""
    platform: str           # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str        # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any]
    round_num: int
    timestamp: str
    
    def to_episode_text(self) -> str:
        """
            Convert activities into text descriptions that can be sent to Zep

                    Adopt a natural language description format that Zep can extract entities and relationships from
                    Do not add simulation-related prefixes to avoid misleading map updates
        """
        # Generate different descriptions based on different action types
        action_descriptions = {
            "CREATE_POST": self._describe_create_post,
            "LIKE_POST": self._describe_like_post,
            "DISLIKE_POST": self._describe_dislike_post,
            "REPOST": self._describe_repost,
            "QUOTE_POST": self._describe_quote_post,
            "FOLLOW": self._describe_follow,
            "CREATE_COMMENT": self._describe_create_comment,
            "LIKE_COMMENT": self._describe_like_comment,
            "DISLIKE_COMMENT": self._describe_dislike_comment,
            "SEARCH_POSTS": self._describe_search,
            "SEARCH_USER": self._describe_search_user,
            "MUTE": self._describe_mute,
        }
        
        describe_func = action_descriptions.get(self.action_type, self._describe_generic)
        description = describe_func()
        
        # Directly return the "agent name: activity description" format without adding the simulation prefix
        return f"{self.agent_name}: {description}"
    
    def _describe_create_post(self) -> str:
        content = self.action_args.get("content", "")
        if content:
            return f"发布了一条帖子：「{content}」"
        return "发布了一条帖子"
    
    def _describe_like_post(self) -> str:
        """Like the post - including the original text and author information of the post"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"点赞了{post_author}的帖子：「{post_content}」"
        elif post_content:
            return f"点赞了一条帖子：「{post_content}」"
        elif post_author:
            return f"点赞了{post_author}的一条帖子"
        return "点赞了一条帖子"
    
    def _describe_dislike_post(self) -> str:
        """Dislike the post - includes the original text and author information of the post"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"踩了{post_author}的帖子：「{post_content}」"
        elif post_content:
            return f"踩了一条帖子：「{post_content}」"
        elif post_author:
            return f"踩了{post_author}的一条帖子"
        return "踩了一条帖子"
    
    def _describe_repost(self) -> str:
        """Repost a post - include original post content and author information"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        
        if original_content and original_author:
            return f"转发了{original_author}的帖子：「{original_content}」"
        elif original_content:
            return f"转发了一条帖子：「{original_content}」"
        elif original_author:
            return f"转发了{original_author}的一条帖子"
        return "转发了一条帖子"
    
    def _describe_quote_post(self) -> str:
        """Quote posts - include original post content, author information, and citation comments"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        quote_content = self.action_args.get("quote_content", "") or self.action_args.get("content", "")
        
        base = ""
        if original_content and original_author:
            base = f"引用了{original_author}的帖子「{original_content}」"
        elif original_content:
            base = f"引用了一条帖子「{original_content}」"
        elif original_author:
            base = f"引用了{original_author}的一条帖子"
        else:
            base = "引用了一条帖子"
        
        if quote_content:
            base += f"，并评论道：「{quote_content}」"
        return base
    
    def _describe_follow(self) -> str:
        """Followed Users - Contains the names of the followed users"""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"关注了用户「{target_user_name}」"
        return "关注了一个用户"
    
    def _describe_create_comment(self) -> str:
        """Post a comment - includes comment content and information about the post being commented on"""
        content = self.action_args.get("content", "")
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if content:
            if post_content and post_author:
                return f"在{post_author}的帖子「{post_content}」下评论道：「{content}」"
            elif post_content:
                return f"在帖子「{post_content}」下评论道：「{content}」"
            elif post_author:
                return f"在{post_author}的帖子下评论道：「{content}」"
            return f"评论道：「{content}」"
        return "发表了评论"
    
    def _describe_like_comment(self) -> str:
        """Like comments - including comment content and author information"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"点赞了{comment_author}的评论：「{comment_content}」"
        elif comment_content:
            return f"点赞了一条评论：「{comment_content}」"
        elif comment_author:
            return f"点赞了{comment_author}的一条评论"
        return "点赞了一条评论"
    
    def _describe_dislike_comment(self) -> str:
        """Dislike comments - includes comment content and author information"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"踩了{comment_author}的评论：「{comment_content}」"
        elif comment_content:
            return f"踩了一条评论：「{comment_content}」"
        elif comment_author:
            return f"踩了{comment_author}的一条评论"
        return "踩了一条评论"
    
    def _describe_search(self) -> str:
        """Search posts - contain search keywords"""
        query = self.action_args.get("query", "") or self.action_args.get("keyword", "")
        return f"搜索了「{query}」" if query else "进行了搜索"
    
    def _describe_search_user(self) -> str:
        """Search user - contains search keywords"""
        query = self.action_args.get("query", "") or self.action_args.get("username", "")
        return f"搜索了用户「{query}」" if query else "搜索了用户"
    
    def _describe_mute(self) -> str:
        """Blocked User - Contains the name of the blocked user"""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"屏蔽了用户「{target_user_name}」"
        return "屏蔽了一个用户"
    
    def _describe_generic(self) -> str:
        # For unknown action types, generate a generic description
        return f"执行了{self.action_type}操作"


class ZepGraphMemoryUpdater:
    """
        Zep map memory updater

            Monitor simulated actions log files and update new agent activities to the Zep graph in real time.
            Group by platform, and send to Zep in batches after every accumulated BATCH_SIZE activity.

            All meaningful actions will be updated to Zep, and action_args will contain complete context information:
            - The original text of the post that was liked/disliked
            - The original text of the post forwarded/quoted
            - Usernames to follow/block
            - The original text of the comment that was liked/disliked
    """
    
    # Batch sending size (how many items are accumulated per platform before sending)
    BATCH_SIZE = 5
    
    # Platform name mapping (for console display)
    PLATFORM_DISPLAY_NAMES = {
        'twitter': 'World 1',
        'reddit': 'World 2',
    }
    
    # Send interval (seconds) to avoid requesting too quickly
    SEND_INTERVAL = 0.5
    
    # Retry configuration
    MAX_RETRIES = 3
    # Second
    
    def __init__(self, graph_id: str, api_key: Optional[str] = None):
        """
            Initialize updater

                    Args:
                        graph_id: Zep graph ID
                        api_key: Zep API Key (optional, read from configuration by default)
        """
        self.graph_id = graph_id
        # api_key reserved for compatibility with legacy signatures only; not required for self-hosted Graphiti (FalkorDB)
        self.api_key = api_key
        self.client = Zep(api_key=self.api_key)
        
        # activity queue
        self._activity_queue: Queue = Queue()
        
        # Activity buffer grouped by platform (each platform accumulates to BATCH_SIZE and then sends in batches)
        self._platform_buffers: Dict[str, List[AgentActivity]] = {
            'twitter': [],
            'reddit': [],
        }
        self._buffer_lock = threading.Lock()
        
        # control flag
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        
        # statistics
        # Number of activities actually added to the queue
        # Number of batches successfully sent to Zep
        # Number of events successfully sent to Zep
        # Number of batches that failed to be sent
        # Number of activities skipped by filtering (DO_NOTHING)
        
        logger.info(f"ZepGraphMemoryUpdater initialized: graph_id={graph_id}, batch_size={self.BATCH_SIZE}")
    
    def _get_platform_display_name(self, platform: str) -> str:
        """Get the display name of the platform"""
        return self.PLATFORM_DISPLAY_NAMES.get(platform.lower(), platform)
    
    def start(self):
        """Start background worker thread"""
        if self._running:
            return

        # Capture locale before spawning background thread
        current_locale = get_locale()

        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            args=(current_locale,),
            daemon=True,
            name=f"ZepMemoryUpdater-{self.graph_id[:8]}"
        )
        self._worker_thread.start()
        logger.info(f"ZepGraphMemoryUpdater started: graph_id={self.graph_id}")
    
    def stop(self):
        """Stop background worker thread"""
        self._running = False
        
        # Send remaining activities
        self._flush_remaining()
        
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)
        
        logger.info(f"ZepGraphMemoryUpdater stopped: graph_id={self.graph_id}, "
                   f"total_activities={self._total_activities}, "
                   f"batches_sent={self._total_sent}, "
                   f"items_sent={self._total_items_sent}, "
                   f"failed={self._failed_count}, "
                   f"skipped={self._skipped_count}")
    
    def add_activity(self, activity: AgentActivity):
        """
            Add an agent activity to the queue

                    All meaningful actions are added to the queue, including:
                    - CREATE_POST (post)
                    - CREATE_COMMENT (comment)
                    - QUOTE_POST (Quote post)
                    - SEARCH_POSTS (search posts)
                    - SEARCH_USER (search user)
                    - LIKE_POST/DISLIKE_POST (like/dislike the post)
                    - REPOST (forward)
                    - FOLLOW
                    - MUTE (mute)
                    - LIKE_COMMENT/DISLIKE_COMMENT (like/dislike comments)

                    action_args will contain complete contextual information (such as the original text of the post, user name, etc.).

                    Args:
                        activity: Agent activity record
        """
        # Skip activities of type DO_NOTHING
        if activity.action_type == "DO_NOTHING":
            self._skipped_count += 1
            return
        
        self._activity_queue.put(activity)
        self._total_activities += 1
        logger.debug(f"Added activity to Zep queue: {activity.agent_name} - {activity.action_type}")
    
    def add_activity_from_dict(self, data: Dict[str, Any], platform: str):
        """
            Add activities from dictionary data

                    Args:
                        data: dictionary data parsed from actions.jsonl
                        platform: platform name (twitter/reddit)
        """
        # Skip entries for event type
        if "event_type" in data:
            return
        
        activity = AgentActivity(
            platform=platform,
            agent_id=data.get("agent_id", 0),
            agent_name=data.get("agent_name", ""),
            action_type=data.get("action_type", ""),
            action_args=data.get("action_args", {}),
            round_num=data.get("round", 0),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )
        
        self.add_activity(activity)
    
    def _worker_loop(self, locale: str = 'zh'):
        """Background Work Loop - Send activities to Zep in batches by platform"""
        set_locale(locale)
        while self._running or not self._activity_queue.empty():
            try:
                # Try to get activity from queue (timeout 1 second)
                try:
                    activity = self._activity_queue.get(timeout=1)
                    
                    # Add the activity to the buffer for the corresponding platform
                    platform = activity.platform.lower()
                    with self._buffer_lock:
                        if platform not in self._platform_buffers:
                            self._platform_buffers[platform] = []
                        self._platform_buffers[platform].append(activity)
                        
                        # Check if the platform has reached the batch size
                        if len(self._platform_buffers[platform]) >= self.BATCH_SIZE:
                            batch = self._platform_buffers[platform][:self.BATCH_SIZE]
                            self._platform_buffers[platform] = self._platform_buffers[platform][self.BATCH_SIZE:]
                            # Release the lock before sending
                            self._send_batch_activities(batch, platform)
                            # Send interval to avoid requesting too quickly
                            time.sleep(self.SEND_INTERVAL)
                    
                except Empty:
                    pass
                    
            except Exception as e:
                logger.error(f"Worker loop exception: {e}")
                time.sleep(1)
    
    def _send_batch_activities(self, activities: List[AgentActivity], platform: str):
        """
            Send activities to the Zep graph in batches (merged into one text)

                    Args:
                        activities: Agent activity list
                        platform: platform name
        """
        if not activities:
            return
        
        # Combine multiple activities into one text, separated by newlines
        episode_texts = [activity.to_episode_text() for activity in activities]
        combined_text = "\n".join(episode_texts)
        
        # Send with retry
        for attempt in range(self.MAX_RETRIES):
            try:
                self.client.graph.add(
                    graph_id=self.graph_id,
                    type="text",
                    data=combined_text
                )
                
                self._total_sent += 1
                self._total_items_sent += len(activities)
                display_name = self._get_platform_display_name(platform)
                logger.info(f"Successfully batch-sent {len(activities)} {display_name} activities to graph {self.graph_id}")
                logger.debug(f"Batch content preview: {combined_text[:200]}...")
                return
                
            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(f"Batch send to Zep failed (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}")
                    time.sleep(self.RETRY_DELAY * (attempt + 1))
                else:
                    logger.error(f"Batch send to Zep failed after {self.MAX_RETRIES} retries: {e}")
                    self._failed_count += 1
    
    def _flush_remaining(self):
        """Send remaining activity in queue and buffer"""
        # First process the remaining activities in the queue and add them to the buffer
        while not self._activity_queue.empty():
            try:
                activity = self._activity_queue.get_nowait()
                platform = activity.platform.lower()
                with self._buffer_lock:
                    if platform not in self._platform_buffers:
                        self._platform_buffers[platform] = []
                    self._platform_buffers[platform].append(activity)
            except Empty:
                break
        
        # Then send the remaining activity in each platform's buffer (even if it is less than BATCH_SIZE items)
        with self._buffer_lock:
            for platform, buffer in self._platform_buffers.items():
                if buffer:
                    display_name = self._get_platform_display_name(platform)
                    logger.info(f"Sending remaining {len(buffer)} activities for {display_name}")
                    self._send_batch_activities(buffer, platform)
            # Clear all buffers
            for platform in self._platform_buffers:
                self._platform_buffers[platform] = []
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics"""
        with self._buffer_lock:
            buffer_sizes = {p: len(b) for p, b in self._platform_buffers.items()}
        
        return {
            "graph_id": self.graph_id,
            "batch_size": self.BATCH_SIZE,
            # The total number of activities added to the queue
            # Number of batches sent successfully
            # Number of successfully sent events
            # Number of batches that failed to be sent
            # Number of activities skipped by filtering (DO_NOTHING)
            "queue_size": self._activity_queue.qsize(),
            # Buffer size for each platform
            "running": self._running,
        }


class ZepGraphMemoryManager:
    """
        Zep map memory updater for managing multiple simulations

            Each simulation can have its own updater instance
    """
    
    _updaters: Dict[str, ZepGraphMemoryUpdater] = {}
    _lock = threading.Lock()
    
    @classmethod
    def create_updater(cls, simulation_id: str, graph_id: str) -> ZepGraphMemoryUpdater:
        """
            Create a map memory updater for the simulation

                    Args:
                        simulation_id: simulation ID
                        graph_id: Zep graph ID

                    Returns:
                        ZepGraphMemoryUpdater instance
        """
        with cls._lock:
            # If it already exists, stop the old one first
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
            
            updater = ZepGraphMemoryUpdater(graph_id)
            updater.start()
            cls._updaters[simulation_id] = updater
            
            logger.info(f"Created graph memory updater: simulation_id={simulation_id}, graph_id={graph_id}")
            return updater
    
    @classmethod
    def get_updater(cls, simulation_id: str) -> Optional[ZepGraphMemoryUpdater]:
        """Get the simulated updater"""
        return cls._updaters.get(simulation_id)
    
    @classmethod
    def stop_updater(cls, simulation_id: str):
        """Stop and remove simulated updater"""
        with cls._lock:
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
                del cls._updaters[simulation_id]
                logger.info(f"Stopped graph memory updater: simulation_id={simulation_id}")
    
    # Flag to prevent repeated calls to stop_all
    _stop_all_done = False
    
    @classmethod
    def stop_all(cls):
        """Stop all updaters"""
        # Prevent repeated calls
        if cls._stop_all_done:
            return
        cls._stop_all_done = True
        
        with cls._lock:
            if cls._updaters:
                for simulation_id, updater in list(cls._updaters.items()):
                    try:
                        updater.stop()
                    except Exception as e:
                        logger.error(f"Failed to stop updater: simulation_id={simulation_id}, error={e}")
                cls._updaters.clear()
            logger.info("Stopped all graph memory updaters")
    
    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all updaters"""
        return {
            sim_id: updater.get_stats() 
            for sim_id, updater in cls._updaters.items()
        }
