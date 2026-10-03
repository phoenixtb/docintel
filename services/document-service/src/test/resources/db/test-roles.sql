-- Roles the Flyway migrations grant to and attach RLS policies to. In the compose stack
-- config/postgres/00-roles.sh creates them; tests must not depend on that file.
CREATE ROLE docintel_app NOLOGIN;
CREATE ROLE docintel_documents NOLOGIN;
