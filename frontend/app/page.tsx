"use client";

import { FormEvent, KeyboardEvent, useRef, useState } from "react";
import AutoHeight from "@/components/AutoHeight";
import BackgroundLines from "@/components/BackgroundLines";
import CameraPreview from "@/components/CameraPreview";
import FlowLines from "@/components/FlowLines";
import HomeOutcome from "@/components/HomeOutcome";
import NotchedPanel from "@/components/NotchedPanel";
import PlanBody, { Noticed } from "@/components/PlanBody";
import ThinkingLabel from "@/components/ThinkingLabel";
import { useTypewriter } from "@/lib/useTypewriter";
import { EXAMPLE_CUES } from "@/lib/scenes";
import { useSession } from "@/providers/FluffySessionProvider";

type View = "intent" | "actions" | "evidence";
const VIEWS: { id: View; label: string }[] = [
  { id: "intent", label: "Intent" },
  { id: "actions", label: "Actions" },
  { id: "evidence", label: "Evidence" },
];

export default function Home() {
  const session = useSession();
  const mainRef = useRef<HTMLElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [view, setView] = useState<View>("intent");
  const [pinned, setPinned] = useState(false);
  const turns = session.state.turns;
  const submitted = turns.length > 0;
  const latest = turns[turns.length - 1];
  const plan = latest?.planId ? session.state.plans[latest.planId] : undefined;
  const actions = (latest?.actionIds ?? []).map((id) => session.state.actions[id]).filter((item) => item != null);
  const busyLabel = session.stageLabel;
  const showThinking = Boolean(busyLabel) && !plan && !latest?.recorded;
  const typewriter = useTypewriter(EXAMPLE_CUES, { active: session.draft.length === 0 && !session.recordedDemo });
  const placeholder = session.recordedDemo ? "Try a recorded line, like “ugh, it's bright”" : typewriter.text + (typewriter.cursor ? "|" : "");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void session.submitText();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      void session.submitText();
    }
  }

  return (
    <main ref={mainRef} className="relative flex min-h-full flex-1 flex-col overflow-x-hidden px-6 py-8 md:px-8">
      <BackgroundLines active={!submitted} targetRef={inputRef} boundsRef={mainRef} />

      {pinned && (
        <div aria-hidden className="pointer-events-none sticky top-0 z-20 -mb-14 bg-gradient-to-b from-white from-60% to-white/0 pb-8 pt-2 text-center">
          <span className="block font-semibold tracking-tighter" style={{ fontSize: "1.5rem" }}>
            Fluffy
          </span>
        </div>
      )}

      <div aria-hidden className="transition-[flex-grow] duration-700 ease-[cubic-bezier(0.22,1,0.36,1)]" style={{ flexGrow: submitted ? 0 : 1 }} />

      <div className="mx-auto flex w-full flex-col gap-6">
        <section className="mx-auto flex w-full max-w-3xl flex-col gap-4">
          <div className="text-center" data-bg-clear>
            <h1
              className="font-semibold tracking-tighter transition-[font-size] duration-700 ease-[cubic-bezier(0.22,1,0.36,1)]"
              style={{ fontSize: submitted ? "1.5rem" : "2.5rem", visibility: pinned ? "hidden" : "visible" }}
              onTransitionEnd={(event) => {
                if (event.propertyName === "font-size") setPinned(submitted);
              }}
            >
              Fluffy
            </h1>
            <p
              className="mb-3 overflow-hidden text-sm text-neutral-500 transition-all duration-500"
              style={{ maxHeight: submitted ? 0 : "2rem", opacity: submitted ? 0 : 1, marginTop: submitted ? 0 : "0.5rem" }}
            >
              A loose cue becomes a quiet change in the home.
            </p>
          </div>

          <form data-bg-clear onSubmit={handleSubmit} className="relative z-10 flex flex-col gap-3">
            <textarea
              ref={inputRef}
              value={session.draft}
              onChange={(event) => session.setDraft(event.target.value)}
              onKeyDown={handleKeyDown}
              rows={submitted ? 2 : 3}
              placeholder={placeholder}
              aria-label="Tell Fluffy"
              className="w-full resize-none rounded-[18px] border border-neutral-200 bg-white p-4 text-base leading-relaxed outline-none placeholder:text-neutral-400"
            />
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-xs text-neutral-400">
                <kbd className="rounded-md bg-neutral-100 px-1.5 py-0.5 text-xs font-medium tracking-tighter text-neutral-500">Enter</kbd> to send ·{" "}
                <kbd className="rounded-md bg-neutral-100 px-1.5 py-0.5 text-xs font-medium tracking-tighter text-neutral-500">Shift</kbd> +{" "}
                <kbd className="rounded-md bg-neutral-100 px-1.5 py-0.5 text-xs font-medium tracking-tighter text-neutral-500">Enter</kbd> for a new line
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  aria-pressed={session.recordedDemo}
                  onClick={() => session.setRecordedDemo(!session.recordedDemo)}
                  className={
                    "h-9 rounded-xl border px-3 text-xs font-medium " +
                    (session.recordedDemo ? "border-neutral-900 bg-neutral-900 text-white" : "border-neutral-200 text-neutral-500")
                  }
                >
                  Recorded demo
                </button>
                <button
                  type="submit"
                  disabled={!session.draft.trim() || session.submitting}
                  className="rounded-xl bg-neutral-900 px-3 py-1.5 text-sm font-medium tracking-tight text-white transition-opacity disabled:opacity-40"
                >
                  {session.submitting ? "Sending..." : "Send"}
                </button>
              </div>
            </div>
          </form>

          <CameraPreview />
          {session.state.notice ? <p className="text-sm text-neutral-500">{session.state.notice}</p> : null}
          {session.state.captions[session.state.captions.length - 1] ? (
            <p className="text-sm text-neutral-700">{session.state.captions[session.state.captions.length - 1].text}</p>
          ) : null}
        </section>

        {showThinking && (
          <div data-bg-clear className="mx-auto w-full max-w-4xl">
            <NotchedPanel className="animate-rise-in" title="Fluffy">
              <div className="relative h-[180px] overflow-hidden rounded-xl border border-neutral-200">
                <p className="pointer-events-none absolute inset-0 flex items-center justify-center">
                  <ThinkingLabel label={busyLabel} />
                </p>
              </div>
            </NotchedPanel>
          </div>
        )}

        {latest && (plan || latest.recorded) && (
          <div data-bg-clear className="mx-auto w-full max-w-4xl">
            <NotchedPanel
              className="animate-rise-in"
              title={latest.recorded ? "Recorded scene" : "Proposed plan"}
              actions={
                plan ? (
                  <div role="tablist" className="flex max-w-full overflow-x-auto rounded-xl bg-neutral-100 p-1 text-sm">
                    {VIEWS.map((item) => (
                      <button
                        key={item.id}
                        role="tab"
                        aria-selected={view === item.id}
                        onClick={() => setView(item.id)}
                        className={"whitespace-nowrap rounded-lg px-3 py-1 transition-colors " + (view === item.id ? "bg-white font-medium" : "text-neutral-500 hover:text-neutral-900")}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                ) : undefined
              }
            >
              {plan ? <PlanBody plan={plan} view={view} /> : <p className="text-sm text-neutral-500">This scene is played from the recorded demo.</p>}
              {busyLabel && !latest.recorded ? (
                <p className="mt-3 flex justify-center">
                  <ThinkingLabel label={busyLabel} />
                </p>
              ) : null}
              {plan ? (
                <details className="mt-4">
                  <summary className="cursor-pointer text-xs text-neutral-400">JSON</summary>
                  <div className="mt-2">
                    <PlanBody plan={plan} view="json" />
                  </div>
                </details>
              ) : null}
            </NotchedPanel>
          </div>
        )}

        {plan && (
          <div data-bg-clear className="mx-auto flex w-full max-w-4xl items-stretch">
            <div className="flex-1 px-5 py-2">
              <Noticed evidence={plan.evidence} done={!latest?.open} />
            </div>
            <div className="relative mr-6 min-h-[120px] w-[28%]">
              <FlowLines active={session.state.inference === "running" || actions.some((action) => action.status === "started")} className="absolute inset-0 h-full w-full" />
            </div>
          </div>
        )}

        {((latest && (actions.length > 0 || latest.recorded)) || session.state.question) && (
          <section data-bg-clear className="animate-rise-in mx-auto w-full max-w-4xl overflow-hidden rounded-[22px] border border-neutral-200 bg-white">
            <AutoHeight>
              <HomeOutcome
                actions={actions}
                recorded={latest?.recorded}
                recordedRows={latest?.recordedRows}
                spoken={latest?.recorded ? latest.recordedSpoken : session.state.captions.at(-1)?.text}
                question={session.state.question}
                answering={session.answering}
                onAnswer={(choice) => void session.answerQuestion(choice)}
                recordedConfirm={latest?.recordedConfirm}
                recordedConfirmed={latest?.recordedConfirmed}
                onRecordedConfirm={() => latest?.requestId && session.confirmRecorded(latest.requestId)}
              />
            </AutoHeight>
          </section>
        )}

        {turns.length > 1 ? (
          <ol className="mx-auto flex w-full max-w-3xl flex-col gap-2 text-sm text-neutral-500">
            {turns.slice(0, -1).map((turn) => (
              <li key={turn.id}>
                <time dateTime={new Date(turn.createdAt).toISOString()}>{new Date(turn.createdAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time>
                {" · "}
                {turn.recorded ? "Recorded" : turn.source} {turn.text ? `· ${turn.text}` : ""}
              </li>
            ))}
          </ol>
        ) : null}
      </div>
      <div aria-hidden className="flex-1" />
    </main>
  );
}
