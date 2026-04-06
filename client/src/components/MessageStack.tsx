import type { InstructionInfo } from "../types/messages.js";

type MessageStackProps = {
  instructionInfo: InstructionInfo;
};

export function MessageStack({ instructionInfo }: MessageStackProps) {
  if (!instructionInfo?.warn && !instructionInfo?.error) return null;

  return (
    <div className="message-stack">
      {instructionInfo.warn && (
        <div className="message-box warn">
          <div className="message-title">Warning</div>
          <div className="message-body">{instructionInfo.warn}</div>
        </div>
      )}
      {instructionInfo.error && (
        <div className="message-box error">
          <div className="message-title">Error</div>
          <div className="message-body">{instructionInfo.error}</div>
        </div>
      )}
    </div>
  );
}
