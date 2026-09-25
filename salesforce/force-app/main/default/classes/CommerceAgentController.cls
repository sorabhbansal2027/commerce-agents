/**
 * Apex controller for the commerce agent LWC components.
 * Exposes @AuraEnabled methods that front CommerceAgentService.
 * with sharing enforces FLS and record visibility for the running user.
 */
public with sharing class CommerceAgentController {

    @TestVisible
    private static CommerceAgentService service = new CommerceAgentService();

    /**
     * Creates a backend session for the current user and returns the session id.
     * Called once from LWC connectedCallback(); result is not cached.
     */
    @AuraEnabled
    public static String getSessionToken(String userId) {
        try {
            return service.createSession(userId);
        } catch (Exception e) {
            throw new AuraHandledException(e.getMessage());
        }
    }

    /**
     * Returns a page of products from the agent backend.
     * cacheable=true — Salesforce CDN caches this for the component's lifetime.
     */
    @AuraEnabled(cacheable=true)
    public static List<Map<String, Object>> getProducts(String sessionId, Integer pageSize) {
        try {
            return service.getProducts(sessionId, pageSize != null ? pageSize : 20);
        } catch (Exception e) {
            throw new AuraHandledException(e.getMessage());
        }
    }

    /**
     * Returns recent OrderSummary records from the agent backend.
     * cacheable=true is intentional: the component re-queries on navigation.
     */
    @AuraEnabled(cacheable=true)
    public static List<Map<String, Object>> getOrderSummaries(String sessionId, Integer recordLimit) {
        try {
            return service.getOrderSummaries(sessionId, recordLimit != null ? recordLimit : 10);
        } catch (Exception e) {
            throw new AuraHandledException(e.getMessage());
        }
    }
}
