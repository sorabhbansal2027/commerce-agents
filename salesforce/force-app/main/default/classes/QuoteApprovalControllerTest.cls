@isTest
private class QuoteApprovalControllerTest {

    // ── Test data setup ───────────────────────────────────────────────────────

    @TestSetup
    static void makeData() {
        Account acc = new Account(Name = 'ACME Corp');
        insert acc;

        Opportunity opp = new Opportunity(
            Name        = 'ACME Deal Q1',
            AccountId   = acc.Id,
            StageName   = 'Proposal/Price Quote',
            CloseDate   = Date.today().addDays(30)
        );
        insert opp;

        Quote q = new Quote(
            Name         = 'Q-001',
            OpportunityId = opp.Id,
            Status       = 'Draft',
            ExpirationDate = Date.today().addDays(14)
        );
        insert q;
    }

    // ── Stub service ──────────────────────────────────────────────────────────

    /**
     * Replaces QuoteApprovalService in controller tests.
     * Avoids calling Approval.process(), which requires a live approval process.
     */
    private class StubService extends QuoteApprovalService {

        public Boolean shouldFail = false;

        public override List<PendingApproval> getPendingApprovals() {
            PendingApproval pa = new PendingApproval();
            pa.workitemId  = 'WORKITEM001';
            pa.quoteId     = 'QUOTE001';
            pa.quoteName   = 'Q-001';
            pa.accountName = 'ACME Corp';
            pa.submittedBy = 'Test User';
            pa.submittedAt = String.valueOf(Date.today());
            pa.grandTotal  = 12500.00;
            return new List<PendingApproval>{ pa };
        }

        public override ApprovalResult approveWorkitem(String workitemId, String comments) {
            if (shouldFail) throw new QuoteApprovalException('Approval process not configured');
            ApprovalResult ar = new ApprovalResult();
            ar.success    = true;
            ar.workitemId = workitemId;
            ar.quoteId    = 'QUOTE001';
            ar.message    = 'Approved successfully';
            return ar;
        }

        public override ApprovalResult rejectWorkitem(String workitemId, String comments) {
            if (shouldFail) throw new QuoteApprovalException('Reject process not configured');
            ApprovalResult ar = new ApprovalResult();
            ar.success    = true;
            ar.workitemId = workitemId;
            ar.quoteId    = 'QUOTE001';
            ar.message    = 'Rejected successfully';
            return ar;
        }
    }

    // ── Tests ─────────────────────────────────────────────────────────────────

    @isTest
    static void getPendingApprovals_returnsList() {
        QuoteApprovalController.service = new StubService();

        Test.startTest();
        List<QuoteApprovalService.PendingApproval> results =
            QuoteApprovalController.getPendingApprovals();
        Test.stopTest();

        System.assertEquals(1, results.size(), 'Expected one pending approval');
        System.assertEquals('Q-001',      results[0].quoteName,   'Quote name mismatch');
        System.assertEquals('ACME Corp',  results[0].accountName, 'Account name mismatch');
        System.assertEquals(12500.00,     results[0].grandTotal,  'Grand total mismatch');
    }

    @isTest
    static void approveQuote_succeeds() {
        QuoteApprovalController.service = new StubService();

        Test.startTest();
        QuoteApprovalService.ApprovalResult result =
            QuoteApprovalController.approveQuote('WORKITEM001', 'LGTM');
        Test.stopTest();

        System.assert(result.success,                         'Expected success = true');
        System.assertEquals('WORKITEM001', result.workitemId, 'Workitem ID mismatch');
        System.assertEquals('Approved successfully', result.message, 'Message mismatch');
    }

    @isTest
    static void rejectQuote_succeeds() {
        QuoteApprovalController.service = new StubService();

        Test.startTest();
        QuoteApprovalService.ApprovalResult result =
            QuoteApprovalController.rejectQuote('WORKITEM001', 'Price too high');
        Test.stopTest();

        System.assert(result.success,                          'Expected success = true');
        System.assertEquals('Rejected successfully', result.message, 'Message mismatch');
    }

    @isTest
    static void approveQuote_serviceError_rethrowsAuraException() {
        StubService stub = new StubService();
        stub.shouldFail = true;
        QuoteApprovalController.service = stub;

        Test.startTest();
        try {
            QuoteApprovalController.approveQuote('WORKITEM001', '');
            System.assert(false, 'Expected AuraHandledException');
        } catch (AuraHandledException ex) {
            System.assert(ex.getMessage().contains('Approval process not configured'),
                          'Unexpected message: ' + ex.getMessage());
        }
        Test.stopTest();
    }

    @isTest
    static void rejectQuote_serviceError_rethrowsAuraException() {
        StubService stub = new StubService();
        stub.shouldFail = true;
        QuoteApprovalController.service = stub;

        Test.startTest();
        try {
            QuoteApprovalController.rejectQuote('WORKITEM001', '');
            System.assert(false, 'Expected AuraHandledException');
        } catch (AuraHandledException ex) {
            System.assert(ex.getMessage().contains('Reject process not configured'),
                          'Unexpected message: ' + ex.getMessage());
        }
        Test.stopTest();
    }

    @isTest
    static void getPendingApprovals_emptyOrg_returnsEmptyList() {
        // Use real service against a scratch org with no approval processes
        // getPendingApprovals() should return [] rather than throw.
        Test.startTest();
        List<QuoteApprovalService.PendingApproval> results =
            QuoteApprovalController.getPendingApprovals();
        Test.stopTest();

        System.assertNotEquals(null, results, 'Result list must not be null');
    }
}
