"""SSE (Server-Sent Events) utility for streaming real-time updates"""

import json
import time
import hashlib
from flask import Response
from .logger import get_logger

logger = get_logger('mirofish.sse')


def _format_sse(event: str, data: dict) -> str:
    """Format a single SSE event"""
    msg = f"event: {event}\n"
    msg += f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    return msg


def _data_fingerprint(data) -> str:
    """Create a fingerprint of data to detect changes"""
    return hashlib.md5(json.dumps(data, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def sse_stream(fetch_fn, stop_condition=None, interval=2, heartbeat_interval=15):
    """
    Create an SSE streaming response.
    
    Args:
        fetch_fn: Callable that returns dict data (matching existing REST response format).
                  Should return None to skip sending, or raise StopIteration to stop.
        stop_condition: Optional callable(data) -> bool. If returns True, send 'complete' event and stop.
        interval: Polling interval in seconds (server-side).
        heartbeat_interval: Seconds between heartbeat events.
    
    Returns:
        Flask Response with text/event-stream content type.
    """
    def generate():
        last_fingerprint = None
        last_heartbeat = time.time()
        
        try:
            while True:
                try:
                    data = fetch_fn()
                except StopIteration:
                    break
                except Exception as e:
                    logger.warning(f"SSE fetch error: {e}")
                    yield _format_sse('error', {'error': str(e)})
                    time.sleep(interval)
                    continue
                
                if data is not None:
                    fingerprint = _data_fingerprint(data)
                    
                    if fingerprint != last_fingerprint:
                        last_fingerprint = fingerprint
                        
                        # Check stop condition
                        if stop_condition and stop_condition(data):
                            yield _format_sse('complete', data)
                            return
                        
                        yield _format_sse('update', data)
                
                # Heartbeat
                now = time.time()
                if now - last_heartbeat >= heartbeat_interval:
                    yield _format_sse('heartbeat', {'ts': int(now)})
                    last_heartbeat = now
                
                time.sleep(interval)
        except GeneratorExit:
            logger.debug("SSE client disconnected")
        except Exception as e:
            logger.error(f"SSE stream error: {e}")
            try:
                yield _format_sse('error', {'error': str(e)})
            except Exception:
                pass
    
    return Response(
        generate(),
        content_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',  # Disable nginx buffering
        }
    )


def sse_incremental_stream(fetch_fn, stop_condition=None, interval=1.5, heartbeat_interval=15):
    """
    SSE stream for incremental log data (agent-log, console-log).
    Unlike sse_stream, this always sends when there's new data (no dedup fingerprint).
    
    Args:
        fetch_fn: Callable(cursor) -> (data_dict, new_cursor, has_new_data).
        stop_condition: Optional callable(data) -> bool.
        interval: Polling interval.
        heartbeat_interval: Heartbeat interval.
    """
    def generate():
        cursor = 0
        last_heartbeat = time.time()
        
        try:
            while True:
                try:
                    data, new_cursor, has_new = fetch_fn(cursor)
                except StopIteration:
                    break
                except Exception as e:
                    logger.warning(f"SSE incremental fetch error: {e}")
                    yield _format_sse('error', {'error': str(e)})
                    time.sleep(interval)
                    continue
                
                if has_new and data is not None:
                    cursor = new_cursor
                    
                    if stop_condition and stop_condition(data):
                        yield _format_sse('complete', data)
                        return
                    
                    yield _format_sse('update', data)
                
                now = time.time()
                if now - last_heartbeat >= heartbeat_interval:
                    yield _format_sse('heartbeat', {'ts': int(now)})
                    last_heartbeat = now
                
                time.sleep(interval)
        except GeneratorExit:
            logger.debug("SSE incremental client disconnected")
        except Exception as e:
            logger.error(f"SSE incremental stream error: {e}")
            try:
                yield _format_sse('error', {'error': str(e)})
            except Exception:
                pass
    
    return Response(
        generate(),
        content_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        }
    )
