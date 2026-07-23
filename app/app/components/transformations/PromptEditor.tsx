import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Dialog } from "@base-ui/react/dialog";
import { Maximize2, X } from "lucide-react";
import { Textarea } from "~/components/ui/textarea";
import { Button } from "~/components/ui/button";
import { cn } from "~/lib/utils";

type Tab = "raw" | "preview";

type PromptEditorProps = {
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
};

function TabToggle({ tab, onTab }: { tab: Tab; onTab: (t: Tab) => void }) {
  return (
    <div className="inline-flex rounded-md border p-0.5 text-xs">
      {(["raw", "preview"] as const).map((t) => (
        <button
          key={t}
          type="button"
          onClick={() => onTab(t)}
          className={cn(
            "rounded px-2 py-1 capitalize",
            tab === t ? "bg-muted text-foreground" : "text-muted-foreground",
          )}
        >
          {t}
        </button>
      ))}
    </div>
  );
}

function EditorPane({
  value,
  onChange,
  onBlur,
  tab,
  rows,
  className,
}: PromptEditorProps & { tab: Tab; rows: number; className?: string }) {
  if (tab === "raw") {
    return (
      <Textarea
        rows={rows}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        className={cn("font-mono text-sm", className)}
      />
    );
  }
  return (
    <div className={cn("overflow-auto rounded-md border p-3", className)}>
      {value.trim() ? (
        <div className="prose prose-sm dark:prose-invert max-w-none">
          <Markdown remarkPlugins={[remarkGfm]}>{value}</Markdown>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Nothing to preview</p>
      )}
    </div>
  );
}

export function PromptEditor({ value, onChange, onBlur }: PromptEditorProps) {
  const [tab, setTab] = useState<Tab>("raw");
  const [fullscreen, setFullscreen] = useState(false);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <TabToggle tab={tab} onTab={setTab} />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setFullscreen(true)}
          aria-label="Expand editor to full screen"
        >
          <Maximize2 className="size-4" />
        </Button>
      </div>
      <EditorPane value={value} onChange={onChange} onBlur={onBlur} tab={tab} rows={4} className="min-h-24" />

      <Dialog.Root open={fullscreen} onOpenChange={(open) => setFullscreen(open)}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/50" />
          <Dialog.Popup className="fixed inset-4 z-50 flex flex-col gap-3 rounded-lg border bg-background p-4 shadow-lg md:inset-10">
            <div className="flex items-center justify-between">
              <Dialog.Title className="text-sm font-medium">Prompt</Dialog.Title>
              <div className="flex items-center gap-2">
                <TabToggle tab={tab} onTab={setTab} />
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setFullscreen(false)}
                  aria-label="Close full screen"
                >
                  <X className="size-4" />
                </Button>
              </div>
            </div>
            <EditorPane
              value={value}
              onChange={onChange}
              onBlur={onBlur}
              tab={tab}
              rows={20}
              className="min-h-0 flex-1 resize-none"
            />
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
