# PostHog post-wizard report

The wizard has completed a deep integration of PostHog into this Django REST Framework project. The Python SDK (`posthog==7.28.0`) was installed via `uv add posthog`. PostHog is initialised once in `CoreConfig.ready()` using environment variables, and the `PosthogContextMiddleware` was added to `MIDDLEWARE` so every request automatically carries session and user context. Four events were instrumented in the Knox-based authentication views covering login, suspicious activity detection, and token revocation.

| Event name | Description | File |
|---|---|---|
| `user_logged_in` | User successfully authenticated and received a Knox token. | `apps/api/autenticacao/views.py` |
| `suspicious_login_detected` | Login was flagged as suspicious due to a change in country. | `apps/api/autenticacao/views.py` |
| `token_revoked` | User revoked a specific authentication token. | `apps/api/autenticacao/views.py` |
| `all_tokens_revoked` | User revoked all authentication tokens except the current one. | `apps/api/autenticacao/views.py` |

## Next steps

We've built some insights and a dashboard for you to keep an eye on user behavior, based on the events we just instrumented:

- [Analytics basics (wizard) — Dashboard](https://us.posthog.com/project/503222/dashboard/1889190)
- [Daily logins (wizard)](https://us.posthog.com/project/503222/insights/gne2Opwy)
- [Logins by device type (wizard)](https://us.posthog.com/project/503222/insights/8OBIJary)
- [Suspicious logins detected (wizard)](https://us.posthog.com/project/503222/insights/lEtEjuBw)
- [Token revocations (wizard)](https://us.posthog.com/project/503222/insights/4R61Oivh)
- [Unique active users (wizard)](https://us.posthog.com/project/503222/insights/Z1mLRMyW)

## Verify before merging

- [ ] Run a full production build (the wizard only verified the files it touched) and fix any lint or type errors introduced by the generated code.
- [ ] Run the test suite — call sites that were rewritten or instrumented may need updated mocks or fixtures.
- [ ] Add `POSTHOG_PROJECT_TOKEN` and `POSTHOG_HOST` to `.env.example` and any bootstrap scripts so collaborators know what to set.
- [ ] Confirm the returning-visitor path also calls `identify` — a handler that only identifies on fresh login can leave returning sessions on anonymous distinct IDs.
- [ ] This project has PostgreSQL, Resend, and Sentry data sources. Run `npx @posthog/wizard warehouse` to connect them to PostHog's data warehouse.

### Agent skill

We've left an agent skill folder in your project. You can use this context for further agent development when using Claude Code. This will help ensure the model provides the most up-to-date approaches for integrating PostHog.
