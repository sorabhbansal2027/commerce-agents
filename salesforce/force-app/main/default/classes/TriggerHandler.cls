/**
 * Abstract base class for the trigger handler pattern.
 * One trigger per object fires: new ConcreteHandler().run()
 * Handlers can be bypassed at runtime without deploying code.
 */
public abstract class TriggerHandler {

    private static Set<String> bypassedHandlers = new Set<String>();

    // ── Bypass API ────────────────────────────────────────────────────────────

    public static void bypass(String handlerName) {
        bypassedHandlers.add(handlerName);
    }

    public static void clearBypass(String handlerName) {
        bypassedHandlers.remove(handlerName);
    }

    public static void clearAllBypasses() {
        bypassedHandlers.clear();
    }

    public static Boolean isBypassed(String handlerName) {
        return bypassedHandlers.contains(handlerName);
    }

    // ── Dispatcher ────────────────────────────────────────────────────────────

    public void run() {
        if (isBypassed(getHandlerName())) {
            return;
        }

        if (Trigger.isBefore) {
            if (Trigger.isInsert)  beforeInsert(Trigger.new);
            if (Trigger.isUpdate)  beforeUpdate(Trigger.new, Trigger.oldMap);
            if (Trigger.isDelete)  beforeDelete(Trigger.old);
            if (Trigger.isUndelete) beforeUndelete(Trigger.new);
        } else {
            if (Trigger.isInsert)  afterInsert(Trigger.new, Trigger.newMap);
            if (Trigger.isUpdate)  afterUpdate(Trigger.new, Trigger.newMap, Trigger.oldMap);
            if (Trigger.isDelete)  afterDelete(Trigger.old, Trigger.oldMap);
            if (Trigger.isUndelete) afterUndelete(Trigger.new, Trigger.newMap);
        }
    }

    // ── Hook overrides (subclasses implement only what they need) ─────────────

    @TestVisible protected virtual void beforeInsert(List<SObject> newList)  {}
    @TestVisible protected virtual void beforeUpdate(List<SObject> newList, Map<Id, SObject> oldMap) {}
    @TestVisible protected virtual void beforeDelete(List<SObject> oldList)  {}
    @TestVisible protected virtual void beforeUndelete(List<SObject> newList) {}

    @TestVisible protected virtual void afterInsert(List<SObject> newList, Map<Id, SObject> newMap) {}
    @TestVisible protected virtual void afterUpdate(List<SObject> newList, Map<Id, SObject> newMap, Map<Id, SObject> oldMap) {}
    @TestVisible protected virtual void afterDelete(List<SObject> oldList, Map<Id, SObject> oldMap) {}
    @TestVisible protected virtual void afterUndelete(List<SObject> newList, Map<Id, SObject> newMap) {}

    // ── Utility ───────────────────────────────────────────────────────────────

    private String getHandlerName() {
        return String.valueOf(this).substringBefore(':');
    }
}
