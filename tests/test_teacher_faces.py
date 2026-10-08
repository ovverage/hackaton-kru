import struct
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from shared.teacher_faces import TeacherFaceEngine, image_dimensions, normalized_embedding, verify_motion


def vector(index=0):
    result = [0.] * 128
    result[index] = 1.
    return result


@pytest.mark.parametrize('bad', [[], [0.] * 128, [float('nan')] * 128, [float('inf')] * 128, [1.] * 127])
def test_bad_embedding_rejected(bad):
    with pytest.raises(ValueError):
        normalized_embedding(bad)


def test_matching_rejects_ambiguity_and_unknown_and_keeps_same_identity_duplicates():
    engine = object.__new__(TeacherFaceEngine)
    rows = [{'teacher_id': 'a', 'name': 'A', 'embedding': vector()}]
    assert engine.match(vector(), rows)['teacher_id'] == 'a'
    assert engine.match(vector(1), rows) is None
    assert engine.match(None, rows) is None
    assert engine.match(vector(), rows * 2)['teacher_id'] == 'a'
    assert engine.match(vector(), rows + [{**rows[0], 'teacher_id': 'b'}]) is None


@pytest.mark.parametrize('poses,stamps,sign', [([0,.3,0],[0,700,1600],1), ([.1,-.2,.1],[0,500,1000],-1)])
def test_motion_front_turn_front(poses, stamps, sign):
    assert verify_motion([{'pose': x} for x in poses], stamps, sign)


@pytest.mark.parametrize('poses,stamps,sign', [([0,0,0],[0,700,1600],1), ([0,-.3,0],[0,700,1600],1), ([0,.3,.3],[0,700,1600],1), ([.4,.7,.4],[0,700,1600],1), ([0,.3,0],[0,100,1600],1), ([0,.3,0],[0,500,700],1), ([0,None,0],[0,700,1600],1), ([0,.3,0],[0,700,float('nan')],1), ([0,.3,0],[0,700,1600],0)])
def test_motion_rejects_wrong_direction_stationary_or_unreliable(poses, stamps, sign):
    assert not verify_motion([{'pose': x} for x in poses], stamps, sign)


def png(w,h):
    return b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', w,h)


def jpeg(w,h):
    return b'\xff\xd8\xff\xe0\x00\x04AB\xff\xc0\x00\x08\x08' + struct.pack('>HH',h,w) + b'\x00'


@pytest.mark.parametrize('encode', [png,jpeg])
def test_image_dimension_guard_before_decode(encode):
    assert image_dimensions(encode(640,480)) == (640,480)
    with pytest.raises(ValueError, match='DIMENSIONS'):
        image_dimensions(encode(10000,10000))
    with pytest.raises(ValueError):
        image_dimensions(encode(640,480)[:-1])


def test_small_second_face_cannot_be_silently_omitted_at_enrolment():
    engine = object.__new__(TeacherFaceEngine)
    normal = np.array([10,10,100,100,30,35,70,35,50,60,30,80,70,80,.99])
    small = normal.copy()
    small[2:4] = 20
    engine.detector = SimpleNamespace(setInputSize=lambda value: None, detect=lambda frame: (None,np.array([normal,small])))
    engine.recognizer = SimpleNamespace(alignCrop=lambda frame, face: frame, feature=lambda crop: vector())
    engine.cv2 = SimpleNamespace(error=RuntimeError)
    engine.lock = threading.RLock()
    frame = np.zeros((200,200,3),np.uint8)
    faces = engine.detect(frame)
    assert len(faces) == 2 and faces[1]['embedding'] is None
    with pytest.raises(ValueError, match='EXACTLY_ONE'):
        engine.encode(frame)
