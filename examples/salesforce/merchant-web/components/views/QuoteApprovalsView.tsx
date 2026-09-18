// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useState } from "react";
import { AskButton, Button, formatDayMonth, KindIcon, Notice, PageHeader, Panel, plural, Skeleton, useResource } from "web-shared";
import { approveQuote, fetchQuoteApprovals, rejectQuote } from "@/lib/api";
import type { PendingQuoteApproval } from "@/lib/types";

function formatMoney(amount: number): string {
  return amount.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function ApprovalRow({
  approval,
  onAction,
  onAskAssistant,
}: {
  approval: PendingQuoteApproval;
  onAction: () => void;
  onAskAssistant: (text: string) => void;
}) {
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleApprove() {
    setBusy("approve");
    setError(null);
    try {
      await approveQuote(approval.workitem_id);
      onAction();
    } catch {
      setError("Approval failed — check the agent assistant for details.");
    } finally {
      setBusy(null);
    }
  }

  async function handleReject() {
    setBusy("reject");
    setError(null);
    try {
      await rejectQuote(approval.workitem_id);
      onAction();
    } catch {
      setError("Rejection failed — check the agent assistant for details.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <li className="flex items-start gap-3 px-[18px] py-4 border-b border-(--line) last:border-0">
      <KindIcon icon="edit" tone="violet" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <div className="text-[13.5px] font-semibold text-(--ink) truncate">{approval.quote_name}</div>
          <div className="shrink-0 text-[15px] font-semibold tabular-nums text-(--ink)">{formatMoney(approval.grand_total)}</div>
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px] text-(--ink-soft)">
          {approval.account_name ? <span>{approval.account_name}</span> : null}
          {approval.submitted_by ? <span>· Submitted by {approval.submitted_by}</span> : null}
          {approval.submitted_at ? <span>· {formatDayMonth(approval.submitted_at)}</span> : null}
          <span className="font-mono text-[11px] opacity-60">{approval.quote_id}</span>
        </div>
        {error ? <p className="mt-1.5 text-[12px] text-(--danger)">{error}</p> : null}
        <div className="mt-3 flex items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            disabled={busy !== null}
            onClick={handleApprove}
            className="bg-(--ok) text-white hover:brightness-105"
          >
            {busy === "approve" ? "Approving…" : "Approve"}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            disabled={busy !== null}
            onClick={handleReject}
            className="text-(--danger) border-(--danger)/40 hover:bg-(--danger)/8"
          >
            {busy === "reject" ? "Rejecting…" : "Reject"}
          </Button>
          <AskButton
            label="Review with assistant"
            onClick={() =>
              onAskAssistant(
                `Review quote ${approval.quote_name} (${approval.quote_id}) for ${approval.account_name ?? "the account"} — total ${formatMoney(approval.grand_total)}. Should I approve or reject it?`
              )
            }
          />
        </div>
      </div>
    </li>
  );
}

export default function QuoteApprovalsView({
  refreshKey,
  onAskAssistant,
}: {
  refreshKey: number;
  onAskAssistant: (text: string) => void;
}) {
  const [localKey, setLocalKey] = useState(0);
  const refresh = () => setLocalKey((k) => k + 1);

  const { data, failed } = useResource(fetchQuoteApprovals, [refreshKey, localKey]);
  const approvals = data?.approvals ?? [];

  return (
    <div className="ac-reveal flex flex-col gap-4">
      <PageHeader
        title="Quote Approvals"
        subtitle={
          data
            ? approvals.length
              ? plural(approvals.length, "pending approval")
              : "No pending approvals"
            : undefined
        }
      />
      {failed && !data ? (
        <Notice>
          The merchant API isn&apos;t reachable, so quote approvals can&apos;t load. Make sure the
          Salesforce backend is running.
        </Notice>
      ) : !data ? (
        <Skeleton className="h-64" />
      ) : (
        <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
          <Panel
            title="Pending approvals"
            subtitle={approvals.length ? String(approvals.length) : undefined}
          >
            {approvals.length === 0 ? (
              <p className="px-[18px] pb-4 text-[13.5px] text-(--ink-soft)">
                No quotes are waiting for your approval.
              </p>
            ) : (
              <ul>
                {approvals.map((approval) => (
                  <ApprovalRow
                    key={approval.workitem_id}
                    approval={approval}
                    onAction={refresh}
                    onAskAssistant={onAskAssistant}
                  />
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="About quote approvals">
            <div className="px-[18px] pb-4 flex flex-col gap-3 text-[13px] text-(--ink-soft)">
              <p>
                Quotes submitted by buyers appear here when they enter your approval queue in
                Salesforce. Approve to activate the quote; reject to send it back.
              </p>
              <p>
                Use <span className="font-medium text-(--ink)">&ldquo;Review with assistant&rdquo;</span> to
                ask the merchant agent for context on the account, pricing, and margin before
                deciding.
              </p>
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}
