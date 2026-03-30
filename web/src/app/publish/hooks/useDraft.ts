import type { PublishDraft } from "../lib/types";
import { DRAFT_KEY, DRAFT_TTL } from "../lib/constants";

export function saveDraft(draft: PublishDraft) {
  sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
}

export function restoreDraft(): PublishDraft | null {
  const raw = sessionStorage.getItem(DRAFT_KEY);
  if (!raw) return null;
  try {
    const draft: PublishDraft = JSON.parse(raw);
    if (Date.now() - draft.createdAt > DRAFT_TTL) {
      sessionStorage.removeItem(DRAFT_KEY);
      return null;
    }
    return draft;
  } catch {
    sessionStorage.removeItem(DRAFT_KEY);
    return null;
  }
}

export function clearDraft() {
  sessionStorage.removeItem(DRAFT_KEY);
}
