# Android Full Backend Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Rewrite-RAG the locally run backend for the existing Android client while PostgreSQL and Redis remain Docker services.

**Architecture:** Android remains an unchanged consumer of its existing Retrofit and SSE contract. Rewrite-RAG owns all API routes, adapting imported legacy API/service code around the v2 workflow. FastAPI runs directly on the Windows host and talks to Docker-published PostgreSQL and Redis through environment configuration.

**Tech Stack:** Kotlin/Compose, Gradle, FastAPI, SQLAlchemy/asyncpg, Redis, LangGraph, PostgreSQL/pgvector.

---

### Task 1: Bring the Android project into Rewrite-RAG

**Files:**
- Create: `android-client/` (complete source tree copied from `D:/knowledge/Multimodal-RAG/android-client/`)
- Create: `android-client/local.properties.example`
- Modify: `.gitignore`

- [ ] **Step 1: Copy the Android source tree without generated build outputs or local SDK configuration.**

  Copy `settings.gradle.kts`, root Gradle files, `gradle/`, and `app/src/` from the source project. Exclude `.gradle/`, every `build/` directory, and `local.properties`.

- [ ] **Step 2: Add a local configuration template.**

  Create `android-client/local.properties.example` with these values:

  ```properties
  # Android SDK path is machine-specific; create local.properties from this file.
  sdk.dir=C\:\\Android\\Sdk
  # Emulator: http://10.0.2.2:8006/ ; physical device: http://<host-lan-ip>:8006/
  BASE_URL=http://10.0.2.2:8006/
  ```

- [ ] **Step 3: Ignore local Android SDK and build outputs.**

  Add these entries to `.gitignore`:

  ```gitignore
  android-client/local.properties
  android-client/.gradle/
  android-client/**/build/
  ```

- [ ] **Step 4: Commit the Android source migration.**

  ```powershell
  git add android-client .gitignore
  git commit -m "feat: migrate Android client into rewrite workspace"
  ```

### Task 2: Configure host-run FastAPI for Android access

**Files:**
- Modify: `run.py`
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Make the server bind host and port configurable.**

  In `run.py`, import `HOST` and `PORT` from `app.core.config`; use `host=HOST` and `port=PORT` in `uvicorn.run`. Keep command-line numeric port as an override.

- [ ] **Step 2: Provide safe local-integration environment values.**

  Replace any credential-like values in `.env.example` with placeholders. Include `OMNICART_HOST=0.0.0.0`, `OMNICART_PORT=8006`, `DATABASE_URL=postgresql+asyncpg://<user>:<password>@localhost:<published-postgres-port>/<database>`, and `REDIS_URL=redis://localhost:<published-redis-port>/0`.

- [ ] **Step 3: Document emulator and physical-device base URLs.**

  Add a README section that distinguishes `10.0.2.2` for the Android emulator from a Windows LAN IP for a physical device; state that `localhost` never refers to the Windows host from Android.

- [ ] **Step 4: Commit the host runtime configuration.**

  ```powershell
  git add run.py .env.example README.md
  git commit -m "feat: configure local Android integration runtime"
  ```

### Task 3: Migrate the Android REST API surface

**Files:**
- Create: `app/api/health.py`, `app/api/products.py`, `app/api/conversations.py`, `app/api/upload.py`, `app/api/auth.py`, `app/api/addresses.py`, `app/api/preferences.py`, `app/api/memories.py`, `app/api/voice.py`
- Create: corresponding files in `app/repositories/`, `app/services/`, `app/schemas/`, and `app/models/` required by those routes
- Modify: `app/api/main.py`

- [ ] **Step 1: Copy each legacy route with its direct schema/service/repository/model dependency from `D:/knowledge/Multimodal-RAG/backend/app/` into the matching Rewrite-RAG package.**

  Preserve existing Android route paths and payload fields: `/api/health`, `/api/products`, `/api/conversations`, `/api/upload`, `/api/auth`, `/api/addresses`, `/api/preferences`, `/api/memories`, and `/api/voice`.

- [ ] **Step 2: Resolve imports against Rewrite-RAG core modules.**

  Reuse Rewrite-RAG `app.core.config`, `app.core.database`, product repositories, cart/order models, and user-profile service whenever types are compatible. Copy a legacy dependency only when Rewrite-RAG has no equivalent.

- [ ] **Step 3: Register all migrated routers in `app/api/main.py`.**

  The application must expose the full Android route set from one FastAPI process; preserve static Web test routing as a development aid.

- [ ] **Step 4: Commit the REST compatibility layer.**

  ```powershell
  git add app/api app/repositories app/services app/schemas app/models
  git commit -m "feat: migrate Android REST API compatibility layer"
  ```

### Task 4: Replace the test-only stream with Android-compatible persisted SSE

**Files:**
- Modify: `app/api/stream.py`
- Create or modify: `app/services/conversation_service.py`
- Create or modify: `app/repositories/conversation_repo.py`
- Modify: `app/api/main.py`

- [ ] **Step 1: Preserve the Android request model.**

  Accept `session_id`, `user_id`, `conversation_id`, `message`, `image_url`, `voice_url`, and `fast_mode`, retaining defaults used by existing Android versions.

- [ ] **Step 2: Replace process-local `_sessions` with the PostgreSQL conversation service.**

  Before invoking `run_agent`, get or create the conversation, load recent messages and `context_snapshot`, and construct `AgentState` from those values. After completion, append the user and assistant messages and persist the latest products, pending question, constraints, and query snapshot.

- [ ] **Step 3: Emit the Android SSE event sequence.**

  Send response text as one-character `token` events, then a `result` JSON event containing `answer`, `products`, and `conversation_id`, followed by `done` with `{}`. On an exception, emit a `result` fallback and `done` so the Android stream always terminates cleanly.

- [ ] **Step 4: Preserve legacy workflow and guide endpoints.**

  Adapt `/api/recommend/v2` and `/api/recommend/guide` to call the Rewrite-RAG graph or maintain compatible JSON responses while the Android client still invokes them.

- [ ] **Step 5: Commit the persisted Android SSE adapter.**

  ```powershell
  git add app/api/stream.py app/services/conversation_service.py app/repositories/conversation_repo.py app/api/main.py
  git commit -m "feat: adapt persisted recommendation SSE for Android"
  ```

### Task 5: Preserve product, cart, checkout, and image interoperability

**Files:**
- Modify: `app/api/cart.py`, `app/api/checkout.py`, `app/api/agent_actions.py`
- Modify or create: `app/api/products.py`, `app/repositories/product_repo.py`
- Modify: `app/schemas/cart.py`, `app/schemas/product.py`

- [ ] **Step 1: Compare migrated payload models with Android Kotlin models.**

  Preserve field names, nullable behavior, and list wrappers consumed by `CartViewModel`, `ProductListViewModel`, `OrderViewModel`, and product detail screens.

- [ ] **Step 2: Make product image URLs routable from Android.**

  Ensure every product returned to Android resolves to an accessible absolute or API-relative image URL and that its route exists in Rewrite-RAG.

- [ ] **Step 3: Preserve user identity through cart and checkout calls.**

  Use incoming `user_id` and authenticated identity when available; retain the legacy demo-user fallback only when Android omits user identity.

- [ ] **Step 4: Commit commerce compatibility changes.**

  ```powershell
  git add app/api app/repositories app/schemas
  git commit -m "feat: preserve Android commerce API compatibility"
  ```

### Task 6: Finish local integration documentation and handoff

**Files:**
- Modify: `README.md`
- Modify: `android-client/app/build.gradle.kts` if needed to read local `BASE_URL`
- Modify: `android-client/app/src/main/AndroidManifest.xml` only if the existing cleartext policy blocks local HTTP

- [ ] **Step 1: Describe the exact local service prerequisites.**

  Document Docker-published PostgreSQL and Redis ports, a local `.env`, a valid Qwen key when mock mode is disabled, and the Android SDK/JDK requirements.

- [ ] **Step 2: Document Android endpoint selection.**

  State the emulator and physical-device URLs, and require Android INTERNET permission plus the existing network-security configuration for HTTP-only local development.

- [ ] **Step 3: Perform only the user-requested manual handoff checks.**

  Do not automatically run tests, Gradle builds, servers, or containers. Report the commands and page flows the user can invoke manually after migration.

- [ ] **Step 4: Commit documentation updates.**

  ```powershell
  git add README.md android-client
  git commit -m "docs: add Android local integration handoff"
  ```
