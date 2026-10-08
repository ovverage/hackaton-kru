from agent.program_check import classify


def test_remote_program_classification_is_case_insensitive_and_deduplicates_services():
    assert classify(["AnyDesk.EXE", "TeamViewer.exe", "teamviewer_service.exe", "RUSTDESK.EXE", "explorer.exe"]) == [
        "AnyDesk", "RustDesk", "TeamViewer"
    ]


def test_remote_program_check_does_not_guess_from_substrings_or_unknown_names():
    assert classify(["not-anydesk.exe", "teamviewer_notes.txt", "exam-browser.exe", "renamed.exe"]) == []
    assert classify([]) == []
