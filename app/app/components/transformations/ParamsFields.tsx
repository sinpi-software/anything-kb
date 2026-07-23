import { Input } from "~/components/ui/input";
import { Label } from "~/components/ui/label";
import { Textarea } from "~/components/ui/textarea";

const KNOWN_KEYS = ["temperature", "top_p", "top_k", "max_tokens"] as const;
type KnownKey = (typeof KNOWN_KEYS)[number];

export type ParamsFieldsValue = {
  known: Record<KnownKey, string>;
  extraJson: string;
};

export const EMPTY_PARAMS: ParamsFieldsValue = {
  known: { temperature: "", top_p: "", top_k: "", max_tokens: "" },
  extraJson: "",
};

export function paramsFromRecord(params: Record<string, unknown> | null): ParamsFieldsValue {
  if (!params) return EMPTY_PARAMS;
  const known: Record<KnownKey, string> = { temperature: "", top_p: "", top_k: "", max_tokens: "" };
  const extra: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params)) {
    if ((KNOWN_KEYS as readonly string[]).includes(k)) known[k as KnownKey] = String(v);
    else extra[k] = v;
  }
  return { known, extraJson: Object.keys(extra).length ? JSON.stringify(extra) : "" };
}

export function ParamsFields({
  value,
  onChange,
}: {
  value: ParamsFieldsValue;
  onChange: (next: ParamsFieldsValue) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-2">
        {KNOWN_KEYS.map((key) => (
          <div key={key} className="flex flex-col gap-1">
            <Label htmlFor={`param-${key}`} className="text-xs text-muted-foreground">{key}</Label>
            <Input
              id={`param-${key}`}
              inputMode="decimal"
              value={value.known[key]}
              onChange={(e) => onChange({ ...value, known: { ...value.known, [key]: e.target.value } })}
            />
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="param-extra" className="text-xs text-muted-foreground">extra (JSON)</Label>
        <Textarea
          id="param-extra"
          rows={2}
          placeholder='{"seed": 42}'
          value={value.extraJson}
          onChange={(e) => onChange({ ...value, extraJson: e.target.value })}
        />
      </div>
    </div>
  );
}
