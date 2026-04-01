"use client";

import { Suspense } from "react";
import { usePublishForm } from "./hooks/usePublishForm";
import { InputHub } from "./components/InputHub";
import { DraftReview } from "./components/DraftReview";
import { AdvancedEdit } from "./components/AdvancedEdit";

/* ------------------------------------------------------------------ */
/*  Main Content — orchestrator for the 3-screen publish flow          */
/*  All state lives in usePublishForm hook.                            */
/*  Screen components: InputHub, DraftReview, AdvancedEdit             */
/* ------------------------------------------------------------------ */

function PublishContent() {
  const form = usePublishForm();

  if (!form.authChecked) {
    return <div className="mx-auto max-w-2xl px-4 py-24 text-center text-muted">Loading...</div>;
  }

  if (form.screen === "input") {
    return <InputHub form={form} />;
  }

  if (form.screen === "draft") {
    return <DraftReview form={form} />;
  }

  return <AdvancedEdit form={form} />;
}

/* ------------------------------------------------------------------ */
/*  Page wrapper                                                       */
/* ------------------------------------------------------------------ */

export default function PublishPage() {
  return (
    <Suspense fallback={<div className="py-24 text-center text-muted">Loading...</div>}>
      <PublishContent />
    </Suspense>
  );
}
