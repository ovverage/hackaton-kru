import csv
import io

import pytest
from fastapi.testclient import TestClient

from backend.proctor.app import create_app
from backend.proctor.db import encode
from shared.rules import State


@pytest.mark.parametrize('lang,first,last', [('ru', 'Ученик', 'Нет'), ('kk', 'Білім алушы', 'Жоқ'), ('en', 'Student', 'No')])
def test_report_language_preserves_user_text_and_csv_safety(tmp_path, lang, first, last):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.headers['X-Requested-With'] = 'Qorgau'
        owner = client.post('/api/auth/setup', json={'name': 'admin', 'password': 'admin'}).json()['user']['id']
        student = '=Студент, Ерлан'
        participant = {'id': 'pc', 'student': student, 'name': 'К301', 'simulated': False, 'state': State().public()}
        with app.state.db.connect(True) as c:
            c.execute('INSERT INTO exams VALUES(?,?,?)', ('exam', owner, encode({'participants': {'pc': participant}})))
        response = client.get(f'/api/exams/exam/report.csv?lang={lang}')
        assert response.status_code == 200
        rows = list(csv.reader(io.StringIO(response.text.lstrip('\ufeff'))))
        assert len(rows[0]) == 11 and rows[0][0] == first
        assert rows[1][0] == "'" + student and rows[1][1] == 'К301' and rows[1][-1] == last
        assert client.get('/api/exams/exam/report.csv?lang=unknown').status_code == 422
