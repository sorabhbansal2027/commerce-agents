/**
 * Service layer for B2B quote approval operations.
 * Queries native ProcessWorkitem records and executes approve/reject
 * via Approval.ProcessWorkitemRequest — no hardcoded IDs or endpoints.
 */
public virtual with sharing class QuoteApprovalService {

    // ── Public types ──────────────────────────────────────────────────────────

    public class PendingApproval {
        @AuraEnabled public String  workitemId;
        @AuraEnabled public String  quoteId;
        @AuraEnabled public String  quoteName;
        @AuraEnabled public String  accountName;
        @AuraEnabled public String  submittedBy;
        @AuraEnabled public String  submittedAt;
        @AuraEnabled public Decimal grandTotal;
    }

    public class ApprovalResult {
        @AuraEnabled public Boolean success;
        @AuraEnabled public String  workitemId;
        @AuraEnabled public String  quoteId;
        @AuraEnabled public String  message;
    }

    public class QuoteApprovalException extends Exception {}

    // ── Public methods ────────────────────────────────────────────────────────

    /**
     * Returns all pending quote approval work-items assigned to the current user
     * or their queue, ordered by submission date ascending.
     */
    public virtual List<PendingApproval> getPendingApprovals() {
        List<ProcessWorkitem> workitems = [
            SELECT
                Id,
                ProcessInstance.TargetObjectId,
                ProcessInstance.TargetObject.Name,
                ProcessInstance.SubmittedById,
                ProcessInstance.SubmittedBy.Name,
                ProcessInstance.CreatedDate,
                Actor.Name,
                OriginalActor.Name
            FROM ProcessWorkitem
            WHERE ProcessInstance.Status = 'Pending'
              AND ProcessInstance.TargetObject.Type = 'Quote'
              AND (ActorId = :UserInfo.getUserId()
                   OR Actor.Type = 'Queue')
            ORDER BY ProcessInstance.CreatedDate ASC
            LIMIT 50
        ];

        if (workitems.isEmpty()) {
            return new List<PendingApproval>();
        }

        // Collect quote IDs for the batch query
        Set<Id> quoteIds = new Set<Id>();
        for (ProcessWorkitem wi : workitems) {
            quoteIds.add(wi.ProcessInstance.TargetObjectId);
        }

        Map<Id, Quote> quoteMap = new Map<Id, Quote>([
            SELECT Id, Name, GrandTotal, Opportunity.Account.Name
            FROM Quote
            WHERE Id IN :quoteIds
        ]);

        List<PendingApproval> results = new List<PendingApproval>();
        for (ProcessWorkitem wi : workitems) {
            Quote q = quoteMap.get(wi.ProcessInstance.TargetObjectId);
            PendingApproval pa = new PendingApproval();
            pa.workitemId  = wi.Id;
            pa.quoteId     = wi.ProcessInstance.TargetObjectId;
            pa.quoteName   = q != null ? q.Name : wi.ProcessInstance.TargetObject.Name;
            pa.accountName = q != null ? q.Opportunity?.Account?.Name : null;
            pa.submittedBy = wi.ProcessInstance.SubmittedBy?.Name;
            pa.submittedAt = wi.ProcessInstance.CreatedDate != null
                             ? String.valueOf(wi.ProcessInstance.CreatedDate.date())
                             : null;
            pa.grandTotal  = q != null ? q.GrandTotal : 0;
            results.add(pa);
        }
        return results;
    }

    /**
     * Approves the work-item identified by workitemId on behalf of the current user.
     */
    public virtual ApprovalResult approveWorkitem(String workitemId, String comments) {
        return processWorkitem(workitemId, 'Approve', comments);
    }

    /**
     * Rejects the work-item identified by workitemId on behalf of the current user.
     */
    public virtual ApprovalResult rejectWorkitem(String workitemId, String comments) {
        return processWorkitem(workitemId, 'Reject', comments);
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private ApprovalResult processWorkitem(String workitemId, String action, String comments) {
        Approval.ProcessWorkitemRequest req = new Approval.ProcessWorkitemRequest();
        req.setWorkitemId(workitemId);
        req.setAction(action);
        req.setComments(comments != null ? comments : '');

        Approval.ProcessResult result = Approval.process(req);

        ApprovalResult ar = new ApprovalResult();
        ar.workitemId = workitemId;
        ar.success    = result.isSuccess();
        ar.quoteId    = result.getEntityId();
        ar.message    = result.isSuccess()
                        ? action + 'd successfully'
                        : String.join((List<String>) result.getErrors(), '; ');

        if (!result.isSuccess()) {
            throw new QuoteApprovalException(ar.message);
        }
        return ar;
    }
}
