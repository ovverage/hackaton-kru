"""Capture the development-only v2 screens; requires Playwright in a QA environment.

Start Vite, then run with --base-url http://127.0.0.1:5173.
No Playwright dependency is added to the application bundle.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

SCENES = {
    'login': 'web-01-login',
    'setup': 'web-01-setup',
    'before': 'web-02-room-before',
    'room': 'web-03-room-live',
    'new': 'web-05-new-session',
    'device': 'web-06-device',
    'event': 'web-07-event',
    'events': 'web-08-events',
    'history': 'web-09-history',
    'computers': 'web-10-computers',
    'rules': 'web-11-rules',
    'teachers': 'web-12-teachers',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:5173')
    parser.add_argument('--browser', help='Optional existing Chromium executable')
    parser.add_argument('--scene', choices=SCENES, action='append')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / 'design/redesign-2026-10-v2/implemented/web'
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=args.browser)
        context = browser.new_context(locale='ru-RU', timezone_id='Asia/Almaty', reduced_motion='reduce')
        for width in (1440, 390):
            for scene, name in SCENES.items():
                if args.scene and scene not in args.scene:
                    continue
                page = context.new_page()
                page.set_viewport_size({'width': width, 'height': 960 if width == 1440 else 844})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'{args.base_url}/?fixture={scene}', wait_until='networkidle')
                page.evaluate('document.fonts.ready')
                page.wait_for_timeout(200)
                if scene in ('login', 'setup'):
                    page.locator('.redesign-auth').wait_for()
                elif scene in ('new', 'device', 'event'):
                    page.get_by_role('dialog').wait_for()
                else:
                    expected = {'before': 'Аудитория', 'room': 'Алгоритмы и структуры данных',
                                'events': 'События', 'history': 'Сеансы и отчёты',
                                'computers': 'Компьютеры', 'rules': 'Правила контроля',
                                'teachers': 'Преподаватели'}
                    page.get_by_role('heading', level=1, name=expected[scene], exact=True).wait_for()
                metrics = page.evaluate('''() => ({
                    viewport: innerWidth,
                    document: document.documentElement.scrollWidth,
                    title: document.querySelector('h1')?.textContent,
                    overflowElements: [...document.querySelectorAll('body *')]
                        .filter(el => el.getBoundingClientRect().right > innerWidth + 1
                          && !el.closest('.table-box,.table-wrap'))
                        .slice(0, 8).map(el => ({tag: el.tagName, class: el.className})),
                    tableScrollers: [...document.querySelectorAll('.table-box,.table-wrap')]
                        .filter(el => el.scrollWidth > el.clientWidth).length,
                })''')
                destination = output / f'{name}-{width}.png'
                has_dialog = page.get_by_role('dialog').count() > 0
                page.screenshot(path=str(destination), full_page=not has_dialog, animations='disabled')
                extras = []
                if has_dialog:
                    scrollers = page.get_by_role('dialog').evaluate("""dialog => [dialog, ...dialog.querySelectorAll('*')].filter(el =>
                        el.scrollHeight > el.clientHeight + 4 && ['auto','scroll'].includes(getComputedStyle(el).overflowY)
                    ).map(el => {el.dataset.captureScroll = 'true'; return true;}).length""")
                    if scrollers:
                        page.locator('[data-capture-scroll]').evaluate_all('els => els.forEach(el => el.scrollTop = el.scrollHeight)')
                        bottom = output / f'{name}-{width}-bottom.png'
                        page.screenshot(path=str(bottom), animations='disabled')
                        extras.append(bottom.name)
                result = {'scene': scene, 'width': width, **metrics, 'errors': errors, 'screenshot': destination.name, 'extra_screenshots': extras}
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                page.close()
        browser.close()
    result_file = output / 'capture-results.json'
    previous = json.loads(result_file.read_text()) if args.scene and result_file.exists() else []
    combined = {(item['scene'], item['width']): item for item in previous + results}
    result_file.write_text(json.dumps(list(combined.values()), ensure_ascii=False, indent=2) + '\n')
    if any(result['document'] > result['viewport'] or result['errors'] for result in results):
        raise SystemExit('Browser errors or horizontal page overflow; inspect capture-results.json')


if __name__ == '__main__':
    main()
