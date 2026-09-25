## Summary

<!-- What does this PR do? Why? Link to Jira/Linear ticket. -->

Fixes: <!-- ticket/issue URL -->

## Change type

- [ ] Apex class / trigger
- [ ] LWC component
- [ ] Salesforce metadata (permission set, named credential, object, flow)
- [ ] CI / GitHub Actions workflow
- [ ] Python backend (FastAPI)
- [ ] Documentation

---

## Apex checklist

_Skip if no Apex changed._

- [ ] No SOQL or DML inside loops — all queries use collections
- [ ] `with sharing` on all controllers and services (justify `without sharing` if used)
- [ ] `@TestVisible` used instead of public on private helpers
- [ ] Named Credential (`Commerce_Agent_API`) used for all callouts — no hardcoded URLs
- [ ] PMD scan passes locally: `pmd check --dir salesforce/force-app --rulesets apex`
- [ ] Test class uses `Test.startTest()` / `Test.stopTest()`
- [ ] Bulk test: at least one test method inserts / processes 200 records
- [ ] Apex code coverage ≥ 75% (paste coverage output below)

<details>
<summary>Coverage output</summary>

```
paste sf apex run test output here
```

</details>

---

## LWC checklist

_Skip if no LWC changed._

- [ ] Jest unit tests added / updated in `__tests__/`
- [ ] `npm run test:unit:coverage` passes with ≥ 80% branch coverage
- [ ] No `window.*` usage — LWS-compatible
- [ ] No direct DOM access outside the component's shadow root
- [ ] `AbortController` usage guarded: `typeof AbortController !== 'undefined'`
- [ ] Tested in a scratch org or sandbox (screenshot attached)
- [ ] Mobile / responsive layout verified at 375 px width

---

## Metadata / deployment checklist

_Skip if no metadata changed._

- [ ] `sfdx-project.json` `sourceApiVersion` matches the org's API version (62.0)
- [ ] Named Credential endpoint updated in Setup after deploy (if applicable)
- [ ] Permission set updated to grant access to new Apex classes
- [ ] Admin / release manager `@`-mentioned below for sign-off

Admin sign-off: @<!-- mention admin -->

---

## Sandbox test

- [ ] Deployed to sandbox: `sf project deploy start --target-org <alias>`
- [ ] Manual smoke test passed (describe below)

<!-- What did you test manually? Include the user story steps you walked through. -->

---

## Screenshots

<!-- Attach screenshots or a Loom recording for any UI change. -->

---

## Additional notes

<!-- Anything reviewers should know: migration steps, rollback plan, dependencies. -->
