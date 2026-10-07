import pytest
from shared.head_review import HeadPoseReview


def observation(**updates):
    value = dict(head_direction='LEFT', head_yaw=-30., head_pitch=0.,
                 head_reference_ready=True, head_away=True)
    value.update(updates)
    return value


def test_head_review_requires_five_seconds_and_emits_once():
    tracker = HeadPoseReview()
    events = [tracker.observe(i / 10, observation(), 1, True, ('exam', 1)) for i in range(101)]
    events = [event for event in events if event]
    assert len(events) == 1
    assert events[0]['duration'] == 5
    assert events[0]['source'] == 'head_pose'


@pytest.mark.parametrize('change', ['gap', 'missing_face', 'pause', 'session', 'epoch', 'unknown'])
def test_interrupted_head_signal_does_not_accumulate(change):
    tracker = HeadPoseReview()
    for i in range(49):
        assert tracker.observe(i / 10, observation(), 1, True, ('exam', 1)) is None
    args = [4.9, observation(), 1, True, ('exam', 1)]
    if change == 'gap':
        args[0] = 6
    elif change == 'missing_face':
        args[2] = 0
    elif change == 'pause':
        args[3] = False
    elif change == 'session':
        args[4] = ('next', 1)
    elif change == 'epoch':
        args[4] = ('exam', 2)
    else:
        args[1] = {}
    assert tracker.observe(*args) is None
    assert tracker.observe(6.1, observation(), 1, True, ('exam', 1)) is None


def test_nonfinite_head_measurement_does_not_count():
    tracker = HeadPoseReview()
    value = observation()
    value['head_yaw'] = float('nan')
    for i in range(100):
        assert tracker.observe(i / 10, value, 1, True, 'session') is None


def extreme():
    return observation(head_yaw=-70., head_tracking_status='tracked', head_extreme=True)


def unavailable(**updates):
    return observation(head_direction='UNKNOWN', head_yaw=None, head_pitch=None,
                       head_away=False, head_tracking_status='unavailable', **updates)


def test_tracking_loss_after_extreme_turn_emits_once_with_history_only():
    tracker = HeadPoseReview()
    assert tracker.observe(0, extreme(), 1, True, 'session') is None
    for index in range(1, 21):
        assert tracker.observe(index / 10, unavailable(), 1, True, 'session') is None
    event = tracker.observe(2.1, unavailable(), 1, True, 'session')
    assert event['source'] == 'head_tracking_loss'
    assert event['start'] == .1 and event['duration'] == 2
    assert event['direction'] is event['head_yaw'] is event['head_pitch'] is None
    assert event['last_known_head_yaw'] == -70
    assert event['last_known_head_pitch'] == event['last_known_head_at'] == 0
    for index in range(22, 100):
        assert tracker.observe(index / 10, unavailable(), 1, True, 'session') is None


@pytest.mark.parametrize('anchor', [
    observation(head_direction='SCREEN', head_yaw=0., head_away=False,
                head_tracking_status='tracked', head_extreme=False),
    observation(head_tracking_status='tracked', head_extreme=False),
    observation(head_yaw=-70., head_extreme=True),  # no explicit tracked signal
    {},
])
def test_ordinary_or_unproven_pose_loss_never_claims_extreme_turn(anchor):
    tracker = HeadPoseReview()
    tracker.observe(0, anchor, 1, True, 'session')
    for index in range(1, 50):
        assert tracker.observe(index / 10, unavailable(), 1, True, 'session') is None


@pytest.mark.parametrize('change', [
    'gap', 'duplicate', 'backwards', 'missing_face', 'second_face', 'pause',
    'session', 'epoch', 'reference', 'reacquired', 'contradictory_angle',
])
def test_tracking_loss_cannot_survive_a_discontinuity(change):
    tracker = HeadPoseReview()
    session = ('exam', 1)
    tracker.observe(0, extreme(), 1, True, session)
    for index in range(1, 20):
        assert tracker.observe(index / 10, unavailable(), 1, True, session) is None
    args = [2., unavailable(), 1, True, session]
    if change == 'gap':
        args[0] = 3.
    elif change == 'duplicate':
        args[0] = 1.9
    elif change == 'backwards':
        args[0] = 1.8
    elif change in ('missing_face', 'second_face'):
        args[2] = 0 if change == 'missing_face' else 2
    elif change == 'pause':
        args[3] = False
    elif change == 'session':
        args[4] = ('new-exam', 1)
    elif change == 'epoch':
        args[4] = ('exam', 2)
    elif change == 'reference':
        args[1]['head_reference_ready'] = False
    elif change == 'reacquired':
        args[1] = observation(head_direction='SCREEN', head_yaw=0., head_away=False,
                              head_tracking_status='tracked', head_extreme=False)
    else:
        args[1]['head_yaw'] = -70.
    assert tracker.observe(*args) is None
    for index in range(1, 40):
        assert tracker.observe(args[0] + index / 10, unavailable(), 1, True, session) is None


def test_expired_extreme_anchor_cannot_start_a_loss_episode():
    tracker = HeadPoseReview()
    tracker.observe(0, extreme(), 1, True, 'session')
    for index in range(40):
        assert tracker.observe(.751 + index / 10, unavailable(), 1, True, 'session') is None


def test_reacquired_extreme_pose_can_start_a_new_independent_loss_episode():
    tracker = HeadPoseReview()
    tracker.observe(0, extreme(), 1, True, 'session')
    for index in range(1, 12):
        assert tracker.observe(index / 10, unavailable(), 1, True, 'session') is None
    assert tracker.observe(1.2, extreme(), 1, True, 'session') is None
    for index in range(13, 33):
        assert tracker.observe(index / 10, unavailable(), 1, True, 'session') is None
    event = tracker.observe(3.3, unavailable(), 1, True, 'session')
    assert event['start'] == 1.3 and event['duration'] == 2
