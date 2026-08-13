# Full CI contract V2

The final PR head must run the complete original validation contract. Optimisations are limited to dependency caching and cancellation of obsolete runs.

Required checks:
1. install complete test toolchain;
2. compile all Python source and tests;
3. Ruff safety lint;
4. static Python safety audit;
5. full referential and governance integrity checks;
6. full pytest suite;
7. audit artifact retained 30 days.

No smoke-only substitute is permitted for the final PR head.