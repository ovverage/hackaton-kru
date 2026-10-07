"""A sustained head turn is review evidence, never an eye-gaze strike."""
import math
from .rules import MAX_OBSERVATION_GAP_SECONDS


class HeadPoseReview:
    def __init__(self):
        self.reset()

    def reset(self):
        self.direction = None
        self.start = None
        self.last = None
        self.emitted = False
        self.session = None
        self.extreme_anchor = None
        self.loss_start = None
        self.loss_emitted = False

    def _clear_turn(self):
        self.direction = None
        self.start = None
        self.emitted = False

    def _clear_loss(self):
        self.extreme_anchor = None
        self.loss_start = None
        self.loss_emitted = False

    def observe(self, at, diagnostics, faces, active, session):
        direction = diagnostics.get('head_direction')
        yaw, pitch = diagnostics.get('head_yaw'), diagnostics.get('head_pitch')
        if (not active or faces != 1 or not diagnostics.get('head_reference_ready')
                or not isinstance(at, (int, float)) or not math.isfinite(at)):
            self.reset()
            return None
        if (self.session != session or self.last is None
                or not 0 < at - self.last <= MAX_OBSERVATION_GAP_SECONDS):
            self.reset()
        self.last, self.session = at, session

        status = diagnostics.get('head_tracking_status')
        measured = status in (None, 'tracked') and all(
            isinstance(value, (int, float)) and math.isfinite(value) for value in (yaw, pitch)
        )
        if not measured:
            self._clear_turn()
            # A disappeared orientation is evidence of tracking loss only. Its
            # preceding strong turn is history, never a current angle/direction.
            unavailable = (status == 'unavailable' and direction == 'UNKNOWN'
                           and yaw is None and pitch is None)
            if not unavailable or self.extreme_anchor is None:
                self._clear_loss()
                return None
            if self.loss_start is None:
                if at - self.extreme_anchor['at'] > MAX_OBSERVATION_GAP_SECONDS:
                    self._clear_loss()
                    return None
                self.loss_start = at
            if not self.loss_emitted and at - self.loss_start >= 2 - 1e-9:
                self.loss_emitted = True
                return dict(
                    start=self.loss_start, end=at, duration=round(at-self.loss_start, 3),
                    direction=None, head_yaw=None, head_pitch=None,
                    last_known_head_yaw=self.extreme_anchor['yaw'],
                    last_known_head_pitch=self.extreme_anchor['pitch'],
                    last_known_head_at=self.extreme_anchor['at'],
                    source='head_tracking_loss',
                    detail='После сильного поворота головы потеряно отслеживание её положения. '
                           'Лицо продолжает обнаруживаться; текущий угол головы, направление глаз '
                           'и факт нарушения не установлены.',
                )
            return None

        # Every measured pose ends a loss episode. Only an explicitly tracked
        # extreme pose can anchor a subsequent loss; a frontal pose cannot.
        self._clear_loss()
        away = (diagnostics.get('head_away') is True
                and direction in ('LEFT', 'RIGHT', 'UP', 'DOWN'))
        if status == 'tracked' and diagnostics.get('head_extreme') is True and away:
            self.extreme_anchor = {'at': at, 'yaw': yaw, 'pitch': pitch}
        if not away:
            self._clear_turn()
            return None
        if direction != self.direction or self.start is None:
            self.start, self.emitted = at, False
        self.direction = direction
        if not self.emitted and at - self.start >= 5 - 1e-9:
            self.emitted = True
            return dict(start=self.start, end=at, duration=round(at-self.start, 3),
                        direction=direction, head_yaw=yaw, head_pitch=pitch,
                        source='head_pose',
                        detail='Устойчивый поворот головы. Направление указано по изображению камеры; '
                               'направление глаз и факт нарушения не установлены.')
        return None
