-- Prio 2 migration: installations, reviews, package_reports tables
-- Run with: psql -U agentnode -d agentnode -f migrate_prio2.sql

-- Enum types
DO $$ BEGIN
    CREATE TYPE install_source AS ENUM ('cli', 'api', 'web', 'sdk', 'adapter');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE install_status AS ENUM ('installed', 'active', 'failed', 'uninstalled');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE install_event_type AS ENUM ('install', 'update', 'rollback');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Installations table
CREATE TABLE IF NOT EXISTS installations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    package_id UUID NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
    package_version_id UUID NOT NULL REFERENCES package_versions(id) ON DELETE CASCADE,
    source install_source NOT NULL,
    status install_status NOT NULL DEFAULT 'installed',
    event_type install_event_type NOT NULL DEFAULT 'install',
    installation_context JSONB DEFAULT '{}',
    installed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ,
    uninstalled_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_installations_user ON installations(user_id);
CREATE INDEX IF NOT EXISTS idx_installations_package ON installations(package_id);

-- Reviews table
CREATE TABLE IF NOT EXISTS reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    package_id UUID NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, package_id)
);

-- Package reports table
CREATE TABLE IF NOT EXISTS package_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    package_id UUID NOT NULL REFERENCES packages(id),
    reporter_user_id UUID NOT NULL REFERENCES users(id),
    reason TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'submitted',
    resolved_by UUID REFERENCES users(id),
    resolution_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
