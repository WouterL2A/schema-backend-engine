# Plan, local sqlite
.\engine\scripts\Plan-Local.ps1

# Apply on empty local sqlite
.\engine\scripts\Apply-Local-Empty.ps1

# Apply on non-empty local sqlite (adds cols/FKs)
.\engine\scripts\Apply-Local-NonEmpty.ps1

# Plan on a remote DB
.\engine\scripts\Plan-Remote.ps1 -DbUrl "postgresql+psycopg2://user:pass@host/dbname"

# Apply on remote, non-empty (MAX INTERLOCKS)
.\engine\scripts\Apply-Remote-NonEmpty.ps1 -DbUrl "postgresql+psycopg2://user:pass@host/dbname"

# Dev nuke & recreate
.\engine\scripts\Recreate-Dev.ps1
