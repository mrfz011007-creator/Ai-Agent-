# Cross-service workflow

1. ChatGPT orchestrates scope and acceptance criteria.
2. Figma owns UI/UX source.
3. GitHub owns source and CI.
4. Linear owns milestones/tasks.
5. GitBook owns project documentation.
6. Replit provides a fast browser-playable prototype loop.
7. Supabase is optional for cloud persistence/telemetry and is not required by offline gameplay.
8. Heavy builds should run in CI/cloud, not on the phone.

## Verification rule
A milestone is not complete until implementation, automated checks, and a concrete runtime artifact are verified.
