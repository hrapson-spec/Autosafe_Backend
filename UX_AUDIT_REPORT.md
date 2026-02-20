# UX Audit & Persona Synthesis Report

## Step 1: Codebase Analysis

### Architecture Overview
*   **Routes**: 
    *   **Backend (`main.py`)**: Defines core API routes like `/api/vehicle` (proxy to DVLA), `/api/risk` (AI model/database lookup), and `/api/leads` (lead capture).
    *   **Frontend (`App.tsx`)**: Utilizes React Router with core views like `/app` (Home) and various `/app/guides/*` SEO pages.
*   **Schema**: Managed via PostgreSQL (`database.py`), storing `mot_risk` (precomputed risk data), `leads` (user details for garage matching), `garages` (mechanic directory), and `lead_assignments`.
*   **State Management**: Handled via simple React `useState` hooks in the top-level `App.tsx` component (managing `selection`, `report`, `loading`, `error`, `postcode`). State is passed down linearly as props to child components (`HeroForm`, `ReportDashboard`).
*   **UI Components**: Focused on conversion. `HeroForm.tsx` captures minimal input (Reg + Postcode). `ReportDashboard.tsx` is highly visual, utilizing `recharts` for a "Reliability Score" pie chart and breaking down "Common Faults" into Red/Yellow/Blue risk levels.

### Core Value Proposition & "Happy Path"
*   **Value Proposition**: "Fix it before they find it." Providing free, instant, AI-driven MOT predictive maintenance insights using DVSA data, ultimately monetizing via Lead Generation to local registered garages.
*   **The Happy Path**:
    1.  User enters Vehicle Registration & Postcode on the homepage (`HeroForm`).
    2.  `services/autosafeApi.ts` fetches vehicle details from the DVLA, then calculates the risk from the PostgreSQL database (falling back to SQLite).
    3.  `App.tsx` renders the `ReportDashboard`.
    4.  User sees a High Risk prediction (e.g., brakes or tyres).
    5.  User clicks the prominent "Find a local garage" Call-To-Action.
    6.  The `GarageFinderModal` opens, capturing their consent and converting them into a stored lead.

---

## Step 2 & 3: Persona Synthesis & Autonomous Testing (Think-Aloud)

### 1. The Intended User: "Anxious Anni"
*   **Profile**: Knows little about cars. Her MOT is due next month, and she fears a massive bill. Matches the happy path perfectly.
*   **File/Component Interaction**: `HeroForm.tsx` -> `ReportDashboard.tsx` -> `GarageFinderModal.tsx`.
*   **Emotional Response**: *Relieved and Empowered*. The clean, 2-field form is easy. The color-coded pie charts and "Good/Fair/Poor" ratings instantly make sense without requiring automotive knowledge.
*   **UX Debt Flag**: None. The system heavily prioritizes clarity for this exact user.

### 2. The Frustrated Professional: "Mechanic Mike"
*   **Profile**: Owns an independent garage. Wants to look up probability rates of specific part failures for incoming cars to pre-order parts or quote repairs.
*   **File/Component Interaction**: `HeroForm.tsx` (Used repeatedly) -> `ReportDashboard.tsx`.
*   **Emotional Response**: *Frustrated and Restricted*. He wants raw data, but gets "loss-framed" B2C consumer risk scores. Every time he checks a car, he has to click "Check Another Vehicle," which completely resets the app state.
*   **UX Debt Flag**: Developer convenience prioritized over power users. The `handleReset` function in `App.tsx` clears all states, forcing him to re-enter his postcode every single time. No bulk search or tabular data view exists.

### 3. The Edge-Case Explorer: "Rural Roy"
*   **Profile**: Lives in a spotty internet area (high latency), driving a rare imported car or classic vehicle.
*   **File/Component Interaction**: `services/autosafeApi.ts` -> `App.tsx` (Error State).
*   **Emotional Response**: *Confused and Abandoned*. The app times out, or his rare car isn't found in the database. He just sees a generic red toast say "Unknown error occurred" or "HTTP 500".
*   **UX Debt Flag**: Exception handling is built for developers. The `catch (err)` block simply passes `err.message` to a generic Alert component. Furthermore, rare cars fallback to a "population average" internally without prominently explaining this to the user (it's hidden in a tiny `note` field).

### 4. The Security-Conscious / Skeptic: "Private Paul"
*   **Profile**: Despises sharing personal data. Doesn't understand why a website needs to know exactly where he lives just to give him public MOT data.
*   **File/Component Interaction**: `HeroForm.tsx` -> Abandons site.
*   **Emotional Response**: *Suspicious*. "Why do they need my postcode just to check my MOT history? Are they selling my data?"
*   **UX Debt Flag**: Business goals (Lead Generation) actively harm user trust. The postcode is demanded *upfront* in `HeroForm` solely so the backend has it ready if they decide to click the Garage Finder CTA later.

---

## Executive Summary: Pain Points & Code Fixes

| Persona | Deduced Pain Point | Specific Code Fix |
| :--- | :--- | :--- |
| **Intended User** (Anni) | Might overlook the "Email Report" feature if scrolling quickly past the dashboard stats. | **Make CTA Sticky**: Implement a sticky bottom bar for the "Email Report / Set Reminder" feature on mobile screens, similar to the existing `StickyCta` component. |
| **Frustrated Pro** (Mike) | "Check Another Vehicle" wipes the entire state. Batch lookups are repetitive and tedious. | **State Retention**: In `App.tsx`'s `handleReset`, preserve the `postcode` state so he doesn't have to re-type it. Consider adding a "Recent Searches" dropdown to local storage. |
| **Edge-Case Explorer** (Roy) | Generic error toast (`err.message`) for timeouts or rare car lookup failures leaves the user stranded. | **Graceful Degration**: Update the `catch` block in `handleCarCheck` (`App.tsx`) to map specific HTTP errors (like 404 or 503) to friendly, actionable UI messages (e.g., "DVLA API is slow, please try again"). |
| **Skeptic** (Paul) | Postcode is demanded upfront in the hero form before any value is demonstrated to the user. | **Deferred Data Collection**: Remove `postcode` from `HeroForm.tsx`. Only request the postcode inside `GarageFinderModal.tsx` when the user actively signals they want local quotes. |
