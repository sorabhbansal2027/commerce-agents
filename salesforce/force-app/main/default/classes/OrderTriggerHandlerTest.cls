@isTest
private class OrderTriggerHandlerTest {

    @TestSetup
    static void makeData() {
        Account acct = new Account(Name = 'Test Buyer Corp');
        insert acct;
    }

    @isTest
    static void afterInsert_syncsOrders_toAgent() {
        MockHttpService mock = new MockHttpService(200, '{"ok":true}');
        OrderService svc = new OrderService(mock);
        OrderTriggerHandler handler = new OrderTriggerHandler(svc);

        List<OrderSummary> orders = buildOrderSummaries(5);
        Test.startTest();
        handler.afterInsert(orders, new Map<Id, SObject>(orders));
        Test.stopTest();

        System.assertEquals(1, mock.callCount, 'Expected exactly one callout for 5 orders');
    }

    @isTest
    static void afterUpdate_notifiesAgent_onStatusChange() {
        MockHttpService mock = new MockHttpService(200, '{"ok":true}');
        OrderService svc = new OrderService(mock);
        OrderTriggerHandler handler = new OrderTriggerHandler(svc);

        List<OrderSummary> oldOrders = buildOrderSummaries(3);
        Map<Id, OrderSummary> oldMap = new Map<Id, OrderSummary>(oldOrders);

        List<OrderSummary> newOrders = oldOrders.deepClone(true, true, true);
        for (OrderSummary os : newOrders) {
            os.Status = 'Fulfilled';
        }

        Test.startTest();
        handler.afterUpdate(newOrders, new Map<Id, SObject>(newOrders), (Map<Id, SObject>) oldMap);
        Test.stopTest();

        System.assertEquals(1, mock.callCount, 'Expected one callout for status-changed orders');
    }

    @isTest
    static void afterUpdate_noCallout_whenStatusUnchanged() {
        MockHttpService mock = new MockHttpService(200, '{"ok":true}');
        OrderService svc = new OrderService(mock);
        OrderTriggerHandler handler = new OrderTriggerHandler(svc);

        List<OrderSummary> orders = buildOrderSummaries(2);
        Map<Id, OrderSummary> oldMap = new Map<Id, OrderSummary>(orders);

        Test.startTest();
        handler.afterUpdate(orders, new Map<Id, SObject>(orders), (Map<Id, SObject>) oldMap);
        Test.stopTest();

        System.assertEquals(0, mock.callCount, 'Expected no callout when status is unchanged');
    }

    @isTest
    static void bypass_preventsHandlerExecution() {
        MockHttpService mock = new MockHttpService(200, '{"ok":true}');
        OrderService svc = new OrderService(mock);
        OrderTriggerHandler handler = new OrderTriggerHandler(svc);

        TriggerHandler.bypass('OrderTriggerHandler');

        List<OrderSummary> orders = buildOrderSummaries(200);

        Test.startTest();
        handler.run();
        Test.stopTest();

        TriggerHandler.clearBypass('OrderTriggerHandler');
        System.assertEquals(0, mock.callCount, 'Bypassed handler should make no callouts');
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private static List<OrderSummary> buildOrderSummaries(Integer count) {
        List<OrderSummary> result = new List<OrderSummary>();
        for (Integer i = 0; i < count; i++) {
            result.add(new OrderSummary(
                OrderNumber = 'ORD-' + String.valueOf(i).leftPad(4, '0'),
                Status      = 'Activated',
                GrandTotalAmount = 100.00 * (i + 1)
            ));
        }
        return result;
    }

    // ── Mock ──────────────────────────────────────────────────────────────────

    private class MockHttpService implements IHttpService {
        public Integer callCount = 0;
        private Integer statusCode;
        private String responseBody;

        MockHttpService(Integer statusCode, String responseBody) {
            this.statusCode   = statusCode;
            this.responseBody = responseBody;
        }

        public HttpResponse send(HttpRequest req) {
            callCount++;
            HttpResponse res = new HttpResponse();
            res.setStatusCode(statusCode);
            res.setBody(responseBody);
            return res;
        }
    }
}
