trigger OrderTrigger on OrderSummary (
    before insert, before update, before delete,
    after insert, after update, after delete, after undelete
) {
    new OrderTriggerHandler().run();
}
