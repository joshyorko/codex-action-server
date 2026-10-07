// Paste into Executor Codemode. All mutations are fenced to this run's new thread.
const runId = `caspr10-${Date.now().toString(36)}`;
const target = "local";
const cwd = "/home/kdlocpanda/.codex/worktrees/cas-production-readiness";
const model = "gpt-6-luna";
const effort = "low";
const results = {};
let threadId;
let secondTurnId;

const unwrap = (raw) => {
  const structured = raw?.structuredContent ?? raw;
  return structured?.result ?? structured;
};

const summarize = (raw) => {
  const envelope = unwrap(raw);
  return {
    error: envelope?.error ?? null,
    operation: envelope?.operation ?? null,
    server: envelope?.connection?.server ?? null,
    result: envelope?.result ?? null,
    effective_configuration: envelope?.effective_configuration ?? null,
    native_methods: (envelope?.receipts ?? [])
      .map((receipt) => receipt?.request?.method)
      .filter(Boolean),
    event_methods: (envelope?.events ?? [])
      .map((event) => event?.method)
      .filter(Boolean),
  };
};

try {
  const created = await tools.codex.create_thread_and_start_turn({
    payload: {
      request_id: `${runId}-create`,
      target,
      cwd,
      model,
      effort,
      text: `CAS production-readiness disposable acceptance ${runId}. Reply exactly CAS_LUNA_FIRST_TURN_OK. Do not use tools or modify files.`,
      wait_for_completion: true,
      wait_seconds: 30,
    },
  });
  const createdEnvelope = unwrap(created);
  const createdResult = createdEnvelope?.result ?? {};
  threadId =
    createdResult?.created?.thread?.id ??
    createdResult?.dispatch?.thread_id ??
    null;
  results.create_and_first_turn = summarize(created);
  if (!threadId) throw new Error("thread creation returned no exact thread ID");

  results.rename = summarize(
    await tools.codex.set_thread_name({
      payload: {
        request_id: `${runId}-name`,
        target,
        cwd,
        thread_id: threadId,
        name: `CAS PR10 disposable ${runId}`,
      },
    }),
  );

  const queue = await tools.codex.add_thread_queue_item({
    payload: {
      request_id: `${runId}-queue`,
      target,
      cwd,
      thread_id: threadId,
      client_user_message_id: `${runId}-queue-message`,
      text: `CAS disposable queue acceptance ${runId}; never execute against another thread.`,
    },
  });
  results.queue_add = summarize(queue);
  const queueResult = unwrap(queue)?.result ?? {};
  const queuedSubmissionId = queueResult?.queuedSubmission?.id;
  if (queuedSubmissionId) {
    results.queue_delete = summarize(
      await tools.codex.delete_thread_queue_item({
        payload: {
          request_id: `${runId}-queue-cleanup`,
          target,
          cwd,
          thread_id: threadId,
          queued_submission_id: queuedSubmissionId,
        },
      }),
    );
  }

  const secondTurn = await tools.codex.start_turn({
    payload: {
      request_id: `${runId}-turn`,
      target,
      cwd,
      thread_id: threadId,
      model,
      effort,
      text: `For the bounded steering check, draft about 1,000 words on a benign software release checklist. Do not use tools or modify files; continue until steered. Run ${runId}.`,
      wait_for_completion: false,
    },
  });
  results.second_turn_start = summarize(secondTurn);
  const turnResult = unwrap(secondTurn)?.result ?? {};
  secondTurnId =
    turnResult?.accepted?.turn?.id ??
    turnResult?.dispatch?.turn_id ??
    null;
  if (!secondTurnId) throw new Error("second turn returned no exact turn ID");

  results.steer = summarize(
    await tools.codex.steer_turn({
      payload: {
        target,
        cwd,
        thread_id: threadId,
        turn_id: secondTurnId,
        text: "Stop now and reply exactly CAS_LUNA_STEERED_OK. Do not continue the draft.",
      },
    }),
  );

  results.turns_after_steer = summarize(
    await tools.codex.list_thread_turns({
      payload: {
        target,
        cwd,
        thread_id: threadId,
        limit: 5,
        sort_direction: "asc",
        items_view: "summary",
      },
    }),
  );
  let turnsEnvelope = unwrap({ structuredContent: { result: results.turns_after_steer } });
  let turns = turnsEnvelope?.result?.data ?? [];
  let active = turns.find((turn) => turn?.id === secondTurnId)?.status === "inProgress";
  if (active) {
    results.interrupt_if_still_active = summarize(
      await tools.codex.interrupt_turn({
        payload: { target, cwd, thread_id: threadId, turn_id: secondTurnId },
      }),
    );
    results.turns_terminal = summarize(
      await tools.codex.list_thread_turns({
        payload: {
          target,
          cwd,
          thread_id: threadId,
          limit: 5,
          sort_direction: "asc",
          items_view: "summary",
        },
      }),
    );
  }
  results.snapshot = summarize(
    await tools.codex.get_thread_snapshot({
      payload: { target, cwd, thread_id: threadId },
    }),
  );
} catch (error) {
  results.harness_error = String(error);
} finally {
  if (threadId) {
    results.thread_delete = summarize(
      await tools.codex.delete_thread({
        payload: {
          request_id: `${runId}-delete`,
          target,
          cwd,
          thread_id: threadId,
        },
      }),
    );
    results.deleted_thread_readback = summarize(
      await tools.codex.read_thread({
        payload: { target, cwd, thread_id: threadId, include_turns: false },
      }),
    );
  }
}

return {
  run_id: runId,
  target,
  cwd,
  requested_model: model,
  requested_effort: effort,
  thread_id: threadId,
  second_turn_id: secondTurnId,
  results,
};
