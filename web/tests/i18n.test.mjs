import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { translations } from "../src/translations.ts";
import {
  apiError,
  formatDate,
  formatDateTime,
  formatTime,
  getLanguage,
  getLocale,
  messageText,
  setLanguage,
  subscribeLanguage,
  t,
  validLanguage,
} from "../src/i18n.ts";
import { eventNames, decisionNames, clock } from "../src/types.ts";
import { commandFailure } from "../src/commands.ts";
import { preparationIssue, controlSignals } from "../src/sessionStatus.ts";

afterEach(() => setLanguage("ru"));
const slots = (text) =>
  [...text.matchAll(/\{\d+\}/g)].map((match) => match[0]).sort();

test("Kazakh dates remain complete even when Chromium lacks its Intl locale data", () => {
  const date = new Date(2026, 9, 8, 9, 5, 7);
  const originalDate = Date.prototype.toLocaleDateString;
  const originalTime = Date.prototype.toLocaleTimeString;
  Date.prototype.toLocaleDateString = () => "M10 8, Thu";
  Date.prototype.toLocaleTimeString = () => "9:05:07 AM";
  try {
    setLanguage("kk");
    assert.equal(
      formatDate(date, { weekday: "long", day: "numeric", month: "long" }),
      "бейсенбі, 8 қазан",
    );
    assert.equal(
      formatDate(date, { day: "numeric", month: "long" }),
      "8 қазан",
    );
    assert.equal(formatDate(date), "08.10.2026");
    assert.equal(
      formatTime(date, { hour: "2-digit", minute: "2-digit" }),
      "09:05",
    );
    assert.equal(formatDateTime(date), "08.10.2026, 09:05:07");
    assert.equal(formatDate(new Date(2026, 0, 1), { month: "long" }), "қаңтар");
    assert.equal(
      formatDate(new Date(2026, 11, 1), { month: "long" }),
      "желтоқсан",
    );
    assert.equal(formatDate(new Date(NaN)), "—");
  } finally {
    Date.prototype.toLocaleDateString = originalDate;
    Date.prototype.toLocaleTimeString = originalTime;
  }
});

test("every catalog phrase has nonempty Kazakh and English with all placeholders", () => {
  assert.ok(Object.keys(translations).length > 550);
  for (const [source, variants] of Object.entries(translations)) {
    assert.equal(variants.length, 2, source);
    for (const [index, phrase] of variants.entries()) {
      assert.ok(phrase.trim(), `${source}, language ${index}`);
      assert.deepEqual(slots(phrase), slots(source), source);
      if (index === 1) assert.doesNotMatch(phrase, /[А-Яа-яЁё]/, source);
    }
  }
});

test("production UI Russian phrases are catalogued, not untranslated JSX", () => {
  const root = path.resolve(import.meta.dirname, "../src");
  const files = fs
    .readdirSync(root, { recursive: true })
    .filter((name) => /\.(tsx?|jsx?)$/.test(name));
  const exempt = new Set(["i18n.ts", "translations.ts"]);
  for (const relative of files) {
    if (exempt.has(relative) || relative.startsWith("dev" + path.sep)) continue;
    const filename = path.join(root, relative);
    const source = ts.createSourceFile(
      filename,
      fs.readFileSync(filename, "utf8"),
      ts.ScriptTarget.Latest,
      true,
    );
    function visit(node) {
      if (ts.isJsxText(node))
        assert.doesNotMatch(node.text, /[А-Яа-яЁё]/, relative);
      if (
        (ts.isStringLiteral(node) ||
          ts.isNoSubstitutionTemplateLiteral(node)) &&
        /[А-Яа-яЁё]/.test(node.text)
      ) {
        assert.ok(
          translations[node.text],
          `${relative}: missing catalog entry ${node.text}`,
        );
        assert.ok(
          !ts.isJsxAttribute(node.parent),
          `${relative}: untranslated attribute ${node.text}`,
        );
        let translated = false;
        for (let ancestor = node.parent; ancestor; ancestor = ancestor.parent) {
          if (
            ts.isCallExpression(ancestor) &&
            ["t", "localizedRecord"].includes(
              ancestor.expression.getText(source),
            )
          )
            translated = true;
          // These three stable direction keys are translated where displayed.
          if (
            ts.isVariableDeclaration(ancestor) &&
            ancestor.name.getText(source) === "directions"
          )
            translated = true;
        }
        assert.ok(translated, `${relative}: untranslated literal ${node.text}`);
      }
      if (ts.isTemplateExpression(node)) {
        assert.doesNotMatch(
          node.head.text +
            node.templateSpans.map((span) => span.literal.text).join(""),
          /[А-Яа-яЁё]/,
          `${relative}: untranslated template`,
        );
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
});

test("language switch updates static event tables, decisions, commands and dates", () => {
  setLanguage("en");
  assert.equal(eventNames.PHONE_DETECTED, "Phone in view");
  assert.equal(eventNames.HEAD_TURN_REVIEW, "Head turn");
  assert.equal(decisionNames.PENDING, "Awaiting review");
  assert.match(commandFailure("CAMERA_UNAVAILABLE"), /Camera unavailable/);
  assert.equal(getLocale(), "en-GB");
  assert.equal(
    clock(1000),
    new Date(1000000).toLocaleTimeString("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }),
  );
  setLanguage("kk");
  assert.equal(eventNames.PHONE_DETECTED, "Кадрда телефон бар");
  assert.equal(eventNames.HEAD_TURN_REVIEW, "Басты бұру");
  assert.equal(decisionNames.PENDING, "Шешімді күтуде");
  assert.match(commandFailure("CAMERA_UNAVAILABLE"), /Камера қолжетімсіз/);
  assert.equal(getLocale(), "kk-KZ");
  setLanguage("ru");
  assert.equal(eventNames.PHONE_DETECTED, "Телефон в кадре");
});

test("names and braces in interpolated user data are preserved exactly", () => {
  const name = "Аружан {1} <img onerror=alert(1)>";
  setLanguage("en");
  assert.equal(t("Удалить {0}", name), `Delete ${name}`);
  assert.equal(t("Образец «{0}» удалён.", name), `Profile “${name}” deleted.`);
  setLanguage("kk");
  assert.equal(t("Удалить {0}", name), `${name} жою`);
});

test("known API errors preserve specific guidance and computer names", () => {
  setLanguage("en");
  assert.equal(
    apiError("Неверное имя или пароль", 401),
    "Incorrect username or password",
  );
  assert.equal(
    apiError("Кабинет-5: завершите текущий сеанс", 409),
    "Кабинет-5: end the current session",
  );
  assert.equal(
    apiError(
      "Этот преподаватель уже добавлен: Аружан. Используйте существующую запись.",
      409,
    ),
    "This teacher is already registered: Аружан. Use the existing profile.",
  );
  assert.equal(
    apiError(
      "Нужна чёткая фотография с одним видимым лицом. UNKNOWN_FACE_ENCODING",
      422,
    ),
    "A clear photo showing exactly one face is required.",
  );
  setLanguage("kk");
  assert.equal(
    apiError("Сначала завершите выбранные сеансы", 409),
    "Алдымен таңдалған сеанстарды аяқтаңыз",
  );
});

test("unknown or structured errors have local guidance without untranslated server text", () => {
  setLanguage("en");
  assert.equal(
    apiError([{ loc: ["password"], msg: "too short" }], 422),
    "Check the entered fields and try again.",
  );
  assert.equal(
    apiError("Неизвестная ошибка", 503),
    "The server is temporarily unavailable. Try again later.",
  );
  setLanguage("kk");
  assert.equal(
    apiError("Unhandled server details", 401),
    "Оқытушы кабинетіне кіріңіз",
  );
});

test("stored notifications translate when switching languages", () => {
  setLanguage("kk");
  const saved = t("Решение сохранено");
  setLanguage("en");
  assert.equal(messageText(saved), "Decision saved");
  setLanguage("ru");
  assert.equal(messageText(saved), "Решение сохранено");
});

test("selector persistence and subscription work even if browser storage is blocked", () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const storage = new Map();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: { setItem: (key, value) => storage.set(key, value) },
  });
  let changes = 0;
  const unsubscribe = subscribeLanguage(() => changes++);
  try {
    setLanguage("kk");
    assert.equal(storage.get("qorgau.language"), "kk");
    assert.equal(getLanguage(), "kk");
    assert.equal(changes, 1);
    assert.equal(validLanguage("kz"), false);
    setLanguage("unknown");
    assert.equal(getLanguage(), "kk");
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      get() {
        throw new Error("blocked");
      },
    });
    assert.doesNotThrow(() => setLanguage("en"));
    assert.equal(getLanguage(), "en");
    assert.equal(changes, 2);
    unsubscribe();
    setLanguage("ru");
    assert.equal(changes, 2);
  } finally {
    unsubscribe();
    if (original) Object.defineProperty(globalThis, "localStorage", original);
    else delete globalThis.localStorage;
  }
});

test("readiness and control signals follow the selected language without changing policy", () => {
  const device = {
    online: false,
    state: { lifecycle: "RUNNING" },
    capabilities: {},
  };
  setLanguage("en");
  assert.equal(preparationIssue(device, "BROWSER"), "Computer disconnected");
  assert.ok(
    controlSignals(device).every(
      (signal) => signal.detail === "No current data" && signal.tone === "idle",
    ),
  );
  setLanguage("kk");
  assert.equal(
    preparationIssue(device, "BROWSER"),
    "Компьютермен байланыс жоқ",
  );
  assert.ok(
    controlSignals(device).every(
      (signal) =>
        signal.detail === "Өзекті деректер жоқ" && signal.tone === "idle",
    ),
  );
});
