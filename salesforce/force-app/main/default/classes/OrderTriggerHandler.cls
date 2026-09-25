/**
 * Trigger handler for the OrderSummary object.
 * Delegates to OrderService — no business logic here.
 */
public with sharing class OrderTriggerHandler extends TriggerHandler {

    private OrderService orderService;

    public OrderTriggerHandler() {
        this.orderService = new OrderService();
    }

    @TestVisible
    private OrderTriggerHandler(OrderService orderService) {
        this.orderService = orderService;
    }

    override protected void afterInsert(List<SObject> newList, Map<Id, SObject> newMap) {
        orderService.syncOrdersToAgent((List<OrderSummary>) newList);
    }

    override protected void afterUpdate(
        List<SObject> newList,
        Map<Id, SObject> newMap,
        Map<Id, SObject> oldMap
    ) {
        orderService.notifyAgentOnStatusChange(
            (List<OrderSummary>) newList,
            (Map<Id, OrderSummary>) oldMap
        );
    }
}
