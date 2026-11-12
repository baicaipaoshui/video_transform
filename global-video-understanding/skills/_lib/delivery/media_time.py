"""Bind delivery metadata to media duration, separately from the last frame PTS."""
import math


def bind_metadata(metadata, probe):
    if probe is None:
        return
    duration = probe.get('duration_s')
    last = probe.get('last_frame_time_s')
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('media.duration_invalid: probe duration must be finite and positive')
    if last is not None and (type(last) not in (int, float) or not math.isfinite(last) or not 0 <= last <= duration):
        raise ValueError('media.last_frame_invalid: last PTS must lie within media duration')
    metadata['duration_s'] = duration
    metadata['source_duration_s'] = duration
    if last is not None:
        metadata['last_frame_time_s'] = last
