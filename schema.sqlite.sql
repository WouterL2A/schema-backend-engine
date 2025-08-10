
CREATE TABLE roles (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(100), 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (name)
)

;


CREATE TABLE users (
	id VARCHAR(36) NOT NULL, 
	email VARCHAR(255), 
	hashed_password VARCHAR(255), 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (email)
)

;


CREATE TABLE sessions (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36), 
	issued_at DATETIME, 
	expires_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
)

;


CREATE TABLE user_roles (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36), 
	role_id VARCHAR(36), 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(role_id) REFERENCES roles (id)
)

;

