export type InstructionInfo = {
  source: "google-drive" | "fallback" | "none";
  text?: string;
  error?: string;
  warn?: string;
};

export type InterviewStepState = {
  current_step: number;
  deep_dive_count: number;
  is_complete: boolean;
  step_summaries: Record<number, string>;
};

export type InterviewStateMessage = {
  type: "interview.state";
  current_step: number;
  deep_dive_count: number;
  is_complete: boolean;
  step_summaries: Record<number, string>;
};

export type InterviewCompleteMessage = {
  type: "interview.complete";
};

export type SessionUpdatePayload = {
  instruction: string;
  scenario: string;
  is_debug: boolean;
};
