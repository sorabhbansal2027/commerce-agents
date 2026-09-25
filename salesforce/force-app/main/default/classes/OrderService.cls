/**
 * Bulkified service layer for OrderSummary operations.
 * All queries and DML use collections — no SOQL or DML inside loops.
 */
public with sharing class OrderService {

    private IHttpService httpService;

    public OrderService() {
        this.httpService = new HttpServiceImpl();
    }

    @TestVisible
    private OrderService(IHttpService httpService) {
        this.httpService = httpService;
    }

    // ── Public API ────────────────────────────────────────────────────────────

    /**
     * Notifies the commerce agent backend when OrderSummary status changes.
     * Only fires for statuses that represent actionable transitions.
     */
    public void notifyAgentOnStatusChange(
        List<OrderSummary> newList,
        Map<Id, OrderSummary> oldMap
    ) {
        List<Id> changedIds = new List<Id>();

        for (OrderSummary os : newList) {
            OrderSummary oldOs = oldMap.get(os.Id);
            if (os.Status != oldOs.Status && isActionableStatus(os.Status)) {
                changedIds.add(os.Id);
            }
        }

        if (changedIds.isEmpty()) {
            return;
        }

        // Single query for all changed orders
        Map<Id, OrderSummary> enrichedOrders = new Map<Id, OrderSummary>([
            SELECT Id, OrderNumber, Status, GrandTotalAmount, BillingEmailAddress,
                   Account.Name, Account.Id
            FROM   OrderSummary
            WHERE  Id IN :changedIds
        ]);

        postOrderEventsToAgent(enrichedOrders.values());
    }

    /**
     * Syncs a batch of newly created orders to the agent backend for indexing.
     */
    public void syncOrdersToAgent(List<OrderSummary> orders) {
        if (orders == null || orders.isEmpty()) {
            return;
        }
        postOrderEventsToAgent(orders);
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private Boolean isActionableStatus(String status) {
        return new Set<String>{
            'Fulfilled', 'Cancelled', 'Shipped', 'Returned'
        }.contains(status);
    }

    private void postOrderEventsToAgent(List<OrderSummary> orders) {
        String endpoint = 'callout:Commerce_Agent_API/internal/order-events';
        HttpRequest req = new HttpRequest();
        req.setEndpoint(endpoint);
        req.setMethod('POST');
        req.setHeader('Content-Type', 'application/json');
        req.setBody(buildPayload(orders));
        req.setTimeout(10000);

        HttpResponse res = httpService.send(req);

        if (res.getStatusCode() >= 400) {
            System.debug(LoggingLevel.WARN,
                'OrderService: agent backend returned ' + res.getStatusCode() +
                ' for ' + orders.size() + ' orders');
        }
    }

    private String buildPayload(List<OrderSummary> orders) {
        List<Map<String, Object>> events = new List<Map<String, Object>>();
        for (OrderSummary os : orders) {
            events.add(new Map<String, Object>{
                'id'     => os.Id,
                'number' => os.OrderNumber,
                'status' => os.Status
            });
        }
        return JSON.serialize(new Map<String, Object>{ 'orders' => events });
    }

    // ── Inner class: production HTTP adapter ──────────────────────────────────

    private class HttpServiceImpl implements IHttpService {
        public HttpResponse send(HttpRequest req) {
            return new Http().send(req);
        }
    }
}
