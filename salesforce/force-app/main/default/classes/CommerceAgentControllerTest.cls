@isTest
private class CommerceAgentControllerTest {

    @isTest
    static void getSessionToken_returnsSessionId() {
        MockHttpService mock = new MockHttpService(
            200, '{"session_id":"sess-abc123"}'
        );
        CommerceAgentController.service = new CommerceAgentService(mock);

        Test.startTest();
        String sessionId = CommerceAgentController.getSessionToken('005xx000001XYZ');
        Test.stopTest();

        System.assertEquals('sess-abc123', sessionId, 'Should return the session_id from the response');
    }

    @isTest
    static void getProducts_returnsList() {
        String body = '{"products":[{"id":"p1","name":"Widget A"},{"id":"p2","name":"Widget B"}]}';
        MockHttpService mock = new MockHttpService(200, body);
        CommerceAgentController.service = new CommerceAgentService(mock);

        Test.startTest();
        List<Map<String, Object>> products = CommerceAgentController.getProducts('sess-abc', 10);
        Test.stopTest();

        System.assertEquals(2, products.size(), 'Should return 2 products');
        System.assertEquals('Widget A', products[0].get('name'));
    }

    @isTest
    static void getOrderSummaries_returnsList() {
        String body = '{"orders":[{"id":"o1","number":"ORD-0001"},{"id":"o2","number":"ORD-0002"}]}';
        MockHttpService mock = new MockHttpService(200, body);
        CommerceAgentController.service = new CommerceAgentService(mock);

        Test.startTest();
        List<Map<String, Object>> orders = CommerceAgentController.getOrderSummaries('sess-abc', 5);
        Test.stopTest();

        System.assertEquals(2, orders.size(), 'Should return 2 orders');
    }

    @isTest
    static void getSessionToken_propagatesAuraException_on4xx() {
        MockHttpService mock = new MockHttpService(401, '{"error":"Unauthorized"}');
        CommerceAgentController.service = new CommerceAgentService(mock);

        Test.startTest();
        Boolean threwException = false;
        try {
            CommerceAgentController.getSessionToken('badUser');
        } catch (AuraHandledException e) {
            threwException = true;
        }
        Test.stopTest();

        System.assert(threwException, 'Expected AuraHandledException on 401 response');
    }

    // ── Mock ──────────────────────────────────────────────────────────────────

    private class MockHttpService implements IHttpService {
        private Integer statusCode;
        private String  responseBody;

        MockHttpService(Integer statusCode, String responseBody) {
            this.statusCode   = statusCode;
            this.responseBody = responseBody;
        }

        public HttpResponse send(HttpRequest req) {
            HttpResponse res = new HttpResponse();
            res.setStatusCode(statusCode);
            res.setBody(responseBody);
            return res;
        }
    }
}
