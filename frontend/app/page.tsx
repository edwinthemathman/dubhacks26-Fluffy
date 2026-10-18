"use client";

import { FormEvent, KeyboardEvent, useRef, useState } from "react";
import AutoHeight from "@/components/AutoHeight";
import BackgroundLines from "@/components/BackgroundLines";
import FlowLines from "@/components/FlowLines";
import HomeOutcome from "@/components/HomeOutcome";
import NotchedPanel from "@/components/NotchedPanel";
import PlanBody, { Noticed } from "@/components/PlanBody";
import ThinkingLabel from "@/components/ThinkingLabel";
import { EXAMPLE_CUES, matchScene, type Scene } from "@/lib/scenes";
import { useTypewriter } from "@/lib/useTypewriter";

const PROPOSE_MS = 900;

const PROPOSE_PHRASES = [
  "Watching the room",
  "Reading the cue",
  "Checking the lamp",
  "Holding the plan",
];

const RUN_PHRASES = [
  "Sending the plan",
  "Waiting on the executor",
  "Applying the change",
];

type View = "intent" | "actions" | "evidence" | "json";
const VIEWS: { id: View; label: string }[] = [
  { id: "intent", label: "Intent" },
  { id: "actions", label: "Actions" },
  { id: "evidence", label: "Evidence" },
  { id: "json", label: "JSON" },
];

type Phase = "idle" | "proposing" | "running" | "done";

export default function Home() {
  const [cue, setCue] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [scene, setScene] = useState<Scene | null>(null);
  const [quiet, setQuiet] = useState(false);
  const [view, setView] = useState<View>("intent");
  const [confirmed, setConfirmed] = useState(false);
  const [pinned, setPinned] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const runToken = useRef(0);

  const typewriter = useTypewriter(EXAMPLE_CUES, { active: cue.length === 0 });
  const placeholder = typewriter.text + (typewriter.cursor ? "|" : "");

  async function propose() {
    const text = cue.trim();
    if (!text || phase === "proposing" || phase === "running") return;
    const token = ++runToken.current;
    setSubmitted(true);
    setPhase("proposing");
    setScene(null);
    setQuiet(false);
    setConfirmed(false);
    setView("intent");
    await new Promise((r) => window.setTimeout(r, PROPOSE_MS));
    if (token !== runToken.current) return;
    const next = matchScene(text);
    if (!next) {
      setQuiet(true);
      setPhase("done");
      return;
    }
    setScene(next);
    setPhase("running");
    await new Promise((r) => window.setTimeout(r, Math.min(next.tookMs, 1600)));
    if (token !== runToken.current) return;
    setPhase("done");
  }

  function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    propose();
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      propose();
    }
  }

  const busy = phase === "proposing" || phase === "running";

  return (
    <main className="flex flex-1 flex-col px-4 py-8">
      <BackgroundLines active={!submitted} targetRef={inputRef} />

      {pinned && (
        <div
          aria-hidden
          className="pointer-events-none fixed inset-x-0 top-0 z-[200] bg-gradient-to-b from-white from-60% to-white/0 px-4 pb-8 pt-8 text-center"
        >
          <span
            className="block font-semibold tracking-tighter"
            style={{ fontSize: "1.5rem" }}
          >
            Fluffy
          </span>
        </div>
      )}

      <div
        aria-hidden
        className="transition-[flex-grow] duration-700 ease-[cubic-bezier(0.22,1,0.36,1)]"
        style={{ flexGrow: submitted ? 0 : 1 }}
      />

      <div className="mx-auto flex w-full flex-col gap-6">
        <section className="mx-auto flex w-full max-w-3xl flex-col gap-4">
          <div className="text-center" data-bg-clear>
            <h1
              className="font-semibold tracking-tighter transition-[font-size] duration-700 ease-[cubic-bezier(0.22,1,0.36,1)]"
              style={{
                fontSize: submitted ? "1.5rem" : "2.5rem",
                visibility: pinned ? "hidden" : "visible",
              }}
              onTransitionEnd={(e) => {
                if (e.propertyName === "font-size") setPinned(submitted);
              }}
            >
              Fluffy
            </h1>
            <p
              className="mb-3 overflow-hidden text-sm text-neutral-500 transition-all duration-500"
              style={{
                maxHeight: submitted ? 0 : "2rem",
                opacity: submitted ? 0 : 1,
                marginTop: submitted ? 0 : "0.5rem",
              }}
            >
              A loose cue becomes a quiet change in the home.
            </p>
          </div>

          <form
            data-bg-clear
            onSubmit={handleSubmit}
            className="relative z-100 flex flex-col gap-3 rounded-2xl focus-within:border-neutral-400"
          >
            <textarea
              ref={inputRef}
              value={cue}
              onChange={(e) => setCue(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={submitted ? 2 : 3}
              placeholder={placeholder}
              aria-label="Tell Fluffy"
              className="-mx-px -mt-px w-[100.25%] resize-none rounded-[18px] border border-neutral-200 bg-white p-4 text-base leading-relaxed outline-none placeholder:text-neutral-400"
            />
            <div className="flex items-center justify-between">
              <span className="text-xs text-neutral-400">
                <kbd className="rounded-md bg-neutral-100 px-1.5 py-0.5 text-xs font-medium tracking-tighter text-neutral-500">
                  Enter
                </kbd>{" "}
                to send ·{" "}
                <kbd className="rounded-md bg-neutral-100 px-1.5 py-0.5 text-xs font-medium tracking-tighter text-neutral-500">
                  Shift
                </kbd>{" "}
                +{" "}
                <kbd className="rounded-md bg-neutral-100 px-1.5 py-0.5 text-xs font-medium tracking-tighter text-neutral-500">
                  Enter
                </kbd>{" "}
                for a new line
              </span>
              <button
                type="submit"
                disabled={!cue.trim() || busy}
                className="rounded-xl bg-neutral-900 px-3 py-1.5 text-sm font-medium tracking-tight text-white transition-opacity disabled:opacity-40"
              >
                {busy ? "Listening..." : "Send"}
              </button>
            </div>
          </form>
        </section>

        {submitted && (
          <div data-bg-clear className="mx-auto w-full max-w-4xl">
            <NotchedPanel
              className="animate-rise-in"
              title={quiet ? "Stayed quiet" : "Proposed plan"}
              actions={
                scene ? (
                  <div
                    role="tablist"
                    className="flex max-w-full overflow-x-auto rounded-xl bg-neutral-100 p-1 text-sm"
                  >
                    {VIEWS.map((v) => (
                      <button
                        key={v.id}
                        role="tab"
                        aria-selected={view === v.id}
                        onClick={() => setView(v.id)}
                        className={
                          "whitespace-nowrap rounded-lg px-3 py-1 transition-colors " +
                          (view === v.id
                            ? "bg-white font-medium"
                            : "text-neutral-500 hover:text-neutral-900")
                        }
                      >
                        {v.label}
                      </button>
                    ))}
                  </div>
                ) : undefined
              }
            >
              {phase === "proposing" ? (
                <div className="relative -m-1.5 -mt-3 h-[240px] overflow-hidden rounded-xl border border-neutral-200">
                  <p className="pointer-events-none absolute inset-0 flex items-center justify-center">
                    <ThinkingLabel phrases={PROPOSE_PHRASES} />
                  </p>
                </div>
              ) : quiet ? (
                <p className="animate-fade-in rounded-xl border border-neutral-200 py-8 text-center text-sm leading-relaxed text-neutral-500">
                  That didn&apos;t sound like a need in the home, so Fluffy stayed silent.
                </p>
              ) : scene ? (
                <div className="animate-fade-in">
                  <PlanBody plan={scene.plan} view={view} />
                </div>
              ) : null}
            </NotchedPanel>
          </div>
        )}

        {scene && (
          <div
            data-bg-clear
            className="animate-fade-in -my-6 mx-auto flex w-full max-w-4xl items-stretch"
          >
            <div className="flex-1 px-5 py-6">
              <Noticed plan={scene.plan} done={phase === "done"} />
            </div>
            <div className="relative mr-6 min-h-[120px] w-[28%]">
              <FlowLines
                active={phase === "running"}
                className="absolute inset-0 h-full w-full"
              />
            </div>
          </div>
        )}

        {scene && phase !== "proposing" && (
          <section
            data-bg-clear
            className="animate-rise-in -mt-4 mx-auto w-full max-w-4xl overflow-hidden rounded-[22px] border border-neutral-200 bg-white"
          >
            <AutoHeight>
              {phase === "running" ? (
                <div className="relative h-[220px]">
                  <p className="pointer-events-none absolute inset-0 flex items-center justify-center">
                    <ThinkingLabel phrases={RUN_PHRASES} />
                  </p>
                </div>
              ) : (
                <div className="animate-fade-in">
                  <HomeOutcome
                    scene={scene}
                    confirmed={confirmed}
                    onConfirm={() => setConfirmed(true)}
                  />
                </div>
              )}
            </AutoHeight>
          </section>
        )}
      </div>

      <div aria-hidden className="flex-1" />
    </main>
  );
}
