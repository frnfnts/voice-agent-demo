import type { InterviewStepState } from "../types/messages.js";
import type { InstructionInfo } from "../types/messages.js";

type DebugPanelProps = {
  interviewState: InterviewStepState | null;
  instructionInfo: InstructionInfo;
  debugLogs: string[];
  stepLabels: string[];
};

export function DebugPanel({
  interviewState,
  instructionInfo,
  debugLogs,
  stepLabels,
}: DebugPanelProps) {
  const instructionLength = instructionInfo?.text?.length || 0;

  return (
    <div className="debug-panel">
      <div className="debug-title">Debug</div>
      <div className="debug-block">
        <div className="debug-subtitle">Interview Progress</div>
        <div className="step-indicator">
          {stepLabels.map((label, i) => {
            const current = interviewState?.current_step;
            const cls =
              i < (current || 0)
                ? "step-done"
                : i === (current || 0)
                ? "step-active"
                : "step-pending";
            return (
              <div key={i} className={`step-chip ${cls}`}>
                <span className="step-num">{i}</span>
                <span className="step-label">{label}</span>
              </div>
            );
          })}
        </div>
        <div className="debug-mono" style={{ marginTop: 4 }}>
          deep_dive: {interviewState?.deep_dive_count} | complete:{" "}
          {String(interviewState?.is_complete)}
        </div>
      </div>
      <div className="debug-grid">
        <div className="debug-row">
          <div className="debug-key">instructionSource</div>
          <div className="debug-value">{instructionInfo?.source}</div>
        </div>
      </div>
      <div className="debug-block">
        <div className="debug-subtitle">Instruction Preview</div>
        <div className="debug-mono">
          {(instructionInfo?.text || "").slice(0, 100)}
          {instructionLength > 100 ? "..." : ""}
        </div>
      </div>
      <div className="debug-block">
        <div className="debug-subtitle">Logs</div>
        <div className="debug-mono">
          {debugLogs.length ? debugLogs.join("\n") : "(no logs)"}
        </div>
      </div>
    </div>
  );
}
