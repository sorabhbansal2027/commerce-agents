/**
 * AuraEnabled controller for the Quote Approvals tab in the merchant LWC.
 * Delegates to QuoteApprovalService; wraps all exceptions in AuraHandledException
 * so the LWC receives a human-readable message rather than an Apex stack trace.
 */
public with sharing class QuoteApprovalController {

    @TestVisible
    private static QuoteApprovalService service = new QuoteApprovalService();

    /**
     * Returns pending quote approval work-items for the current user.
     * Not cacheable — approval state changes on every action.
     */
    @AuraEnabled
    public static List<QuoteApprovalService.PendingApproval> getPendingApprovals() {
        try {
            return service.getPendingApprovals();
        } catch (Exception e) {
            throw new AuraHandledException(e.getMessage());
        }
    }

    /**
     * Approves the given work-item on behalf of the current user.
     */
    @AuraEnabled
    public static QuoteApprovalService.ApprovalResult approveQuote(String workitemId, String comments) {
        try {
            return service.approveWorkitem(workitemId, comments);
        } catch (Exception e) {
            throw new AuraHandledException(e.getMessage());
        }
    }

    /**
     * Rejects the given work-item on behalf of the current user.
     */
    @AuraEnabled
    public static QuoteApprovalService.ApprovalResult rejectQuote(String workitemId, String comments) {
        try {
            return service.rejectWorkitem(workitemId, comments);
        } catch (Exception e) {
            throw new AuraHandledException(e.getMessage());
        }
    }
}
