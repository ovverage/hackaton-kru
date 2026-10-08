import { useSyncExternalStore } from "react";
import { Languages } from "lucide-react";
import {
  getLanguage,
  languageNames,
  setLanguage,
  subscribeLanguage,
  t,
  type Language,
} from "./i18n";

export function useLanguage() {
  return useSyncExternalStore(
    subscribeLanguage,
    getLanguage,
    () => "ru" as Language,
  );
}
export default function LanguageSelector() {
  const language = useLanguage();
  return (
    <label className="language-selector">
      <Languages size={17} aria-hidden="true" />
      <span className="sr-only">{t("Язык интерфейса")}</span>
      <select
        value={language}
        onChange={(event) => setLanguage(event.target.value as Language)}
        aria-label={t("Язык интерфейса")}
      >
        {Object.entries(languageNames).map(([code, name]) => (
          <option key={code} value={code} lang={code}>
            {name}
          </option>
        ))}
      </select>
    </label>
  );
}
