-- The existing project administrator already has ADMIN OPTION for this role.
-- Allow explicit SET ROLE for least-privilege tests. Keep implicit inheritance disabled.
GRANT portfolio_os_runtime TO postgres WITH SET TRUE;
