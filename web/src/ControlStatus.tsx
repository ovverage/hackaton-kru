import { t } from "./i18n.ts";
import { Camera, Eye, ShieldCheck } from "lucide-react";
import { controlSignals } from "./sessionStatus";
import type { Device } from "./types";

const icons = [Camera, Eye, ShieldCheck];
export default function ControlStatus({ device }: { device: Device }) {
  return (
    <div
      className="control-signals"
      aria-label={t("Состояние контроля: {0}", device.name)}
    >
      {controlSignals(device).map((signal, index) => {
        const Icon = icons[index];
        return (
          <span
            key={signal.label}
            className={`control-signal ${signal.tone}`}
            title={signal.detail}
          >
            <Icon size={13} aria-hidden="true" />
            <span aria-hidden="true">{signal.label}</span>
            <span className="sr-only">
              {signal.label}: {signal.detail}
            </span>
          </span>
        );
      })}
    </div>
  );
}
