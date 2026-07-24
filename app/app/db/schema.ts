import { pgTable, varchar, foreignKey, unique, text, boolean, uuid, timestamp, index, integer, jsonb } from "drizzle-orm/pg-core"
import { sql } from "drizzle-orm"



export const alembicVersion = pgTable("alembic_version", {
	versionNum: varchar("version_num", { length: 32 }).primaryKey().notNull(),
});

export const users = pgTable("users", {
	name: text().notNull(),
	email: text().notNull(),
	emailVerified: boolean("email_verified").default(false).notNull(),
	passwordHash: text("password_hash").notNull(),
	isAdmin: boolean("is_admin").default(false).notNull(),
	createdById: uuid("created_by_id"),
	updatedById: uuid("updated_by_id"),
	id: uuid().defaultRandom().primaryKey().notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	foreignKey({
			columns: [table.createdById],
			foreignColumns: [table.id],
			name: "users_created_by_id_fkey"
		}),
	foreignKey({
			columns: [table.updatedById],
			foreignColumns: [table.id],
			name: "users_updated_by_id_fkey"
		}),
	unique("users_email_key").on(table.email),
]);

export const appSettings = pgTable("app_settings", {
	settingName: text("setting_name").notNull(),
	settingValue: text("setting_value"),
	createdById: uuid("created_by_id"),
	updatedById: uuid("updated_by_id"),
	id: uuid().defaultRandom().primaryKey().notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	foreignKey({
			columns: [table.createdById],
			foreignColumns: [users.id],
			name: "app_settings_created_by_id_fkey"
		}),
	foreignKey({
			columns: [table.updatedById],
			foreignColumns: [users.id],
			name: "app_settings_updated_by_id_fkey"
		}),
	unique("app_settings_setting_name_key").on(table.settingName),
]);

export const orgs = pgTable("orgs", {
	name: text().notNull(),
	charter: text(),
	createdById: uuid("created_by_id"),
	updatedById: uuid("updated_by_id"),
	id: uuid().defaultRandom().primaryKey().notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	foreignKey({
			columns: [table.createdById],
			foreignColumns: [users.id],
			name: "orgs_created_by_id_fkey"
		}),
	foreignKey({
			columns: [table.updatedById],
			foreignColumns: [users.id],
			name: "orgs_updated_by_id_fkey"
		}),
]);

export const artifacts = pgTable("artifacts", {
	orgId: uuid("org_id"),
	refTableName: varchar("ref_table_name").notNull(),
	refTableId: uuid("ref_table_id").notNull(),
	type: text().notNull(),
	data: text().notNull(),
	id: uuid().defaultRandom().primaryKey().notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	index("ix_artifacts_type").using("btree", table.type.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.orgId],
			foreignColumns: [orgs.id],
			name: "artifacts_org_id_fkey"
		}),
]);

export const orgSettings = pgTable("org_settings", {
	orgId: uuid("org_id").notNull(),
	settingName: text("setting_name").notNull(),
	settingValue: text("setting_value"),
	createdById: uuid("created_by_id"),
	updatedById: uuid("updated_by_id"),
	id: uuid().defaultRandom().primaryKey().notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	foreignKey({
			columns: [table.createdById],
			foreignColumns: [users.id],
			name: "org_settings_created_by_id_fkey"
		}),
	foreignKey({
			columns: [table.orgId],
			foreignColumns: [orgs.id],
			name: "org_settings_org_id_fkey"
		}),
	foreignKey({
			columns: [table.updatedById],
			foreignColumns: [users.id],
			name: "org_settings_updated_by_id_fkey"
		}),
	unique("org_settings_org_id_setting_name_key").on(table.orgId, table.settingName),
]);

export const transformations = pgTable("transformations", {
	orgId: uuid("org_id").notNull(),
	position: integer().default(0).notNull(),
	type: text().notNull(),
	model: text(),
	prompt: text().notNull(),
	params: jsonb(),
	createdById: uuid("created_by_id"),
	updatedById: uuid("updated_by_id"),
	id: uuid().defaultRandom().primaryKey().notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
	name: text().notNull(),
	gate: jsonb(),
}, (table) => [
	foreignKey({
			columns: [table.createdById],
			foreignColumns: [users.id],
			name: "transformations_created_by_id_fkey"
		}),
	foreignKey({
			columns: [table.orgId],
			foreignColumns: [orgs.id],
			name: "transformations_org_id_fkey"
		}),
	foreignKey({
			columns: [table.updatedById],
			foreignColumns: [users.id],
			name: "transformations_updated_by_id_fkey"
		}),
	unique("transformations_org_id_position_key").on(table.orgId, table.position),
	unique("transformations_org_id_name_key").on(table.orgId, table.name),
]);

export const orgUsers = pgTable("org_users", {
	orgId: uuid("org_id").notNull(),
	userId: uuid("user_id").notNull(),
	role: text().notNull(),
	createdById: uuid("created_by_id"),
	updatedById: uuid("updated_by_id"),
	id: uuid().defaultRandom().primaryKey().notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	foreignKey({
			columns: [table.createdById],
			foreignColumns: [users.id],
			name: "org_users_created_by_id_fkey"
		}),
	foreignKey({
			columns: [table.orgId],
			foreignColumns: [orgs.id],
			name: "org_users_org_id_fkey"
		}),
	foreignKey({
			columns: [table.updatedById],
			foreignColumns: [users.id],
			name: "org_users_updated_by_id_fkey"
		}),
	foreignKey({
			columns: [table.userId],
			foreignColumns: [users.id],
			name: "org_users_user_id_fkey"
		}),
]);

export const rssFeeds = pgTable("rss_feeds", {
	orgId: uuid("org_id").notNull(),
	url: text().notNull(),
	title: text(),
	lastFetchedAt: timestamp("last_fetched_at", { mode: 'string' }),
	active: boolean().default(false).notNull(),
	createdById: uuid("created_by_id"),
	updatedById: uuid("updated_by_id"),
	id: uuid().defaultRandom().primaryKey().notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	foreignKey({
			columns: [table.createdById],
			foreignColumns: [users.id],
			name: "rss_feeds_created_by_id_fkey"
		}),
	foreignKey({
			columns: [table.orgId],
			foreignColumns: [orgs.id],
			name: "rss_feeds_org_id_fkey"
		}),
	foreignKey({
			columns: [table.updatedById],
			foreignColumns: [users.id],
			name: "rss_feeds_updated_by_id_fkey"
		}),
]);

export const wikiPages = pgTable("wiki_pages", {
	orgId: uuid("org_id").notNull(),
	title: text().notNull(),
	content: text(),
	createdById: uuid("created_by_id"),
	updatedById: uuid("updated_by_id"),
	id: uuid().defaultRandom().primaryKey().notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	foreignKey({
			columns: [table.createdById],
			foreignColumns: [users.id],
			name: "wiki_pages_created_by_id_fkey"
		}),
	foreignKey({
			columns: [table.orgId],
			foreignColumns: [orgs.id],
			name: "wiki_pages_org_id_fkey"
		}),
	foreignKey({
			columns: [table.updatedById],
			foreignColumns: [users.id],
			name: "wiki_pages_updated_by_id_fkey"
		}),
]);

export const rssFeedItems = pgTable("rss_feed_items", {
	feedId: uuid("feed_id").notNull(),
	dedupKey: text("dedup_key").notNull(),
	title: text().notNull(),
	link: text().notNull(),
	content: text(),
	status: text().default('pending').notNull(),
	id: uuid().defaultRandom().primaryKey().notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	foreignKey({
			columns: [table.feedId],
			foreignColumns: [rssFeeds.id],
			name: "rss_feed_items_feed_id_fkey"
		}),
	unique("rss_feed_items_feed_id_dedup_key_key").on(table.feedId, table.dedupKey),
]);

export const transformRuns = pgTable("transform_runs", {
	transformationId: uuid("transformation_id").notNull(),
	inputArtifactId: uuid("input_artifact_id").notNull(),
	outputArtifactId: uuid("output_artifact_id"),
	status: text().default('pending').notNull(),
	errorMessage: text("error_message"),
	id: uuid().defaultRandom().primaryKey().notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	foreignKey({
			columns: [table.inputArtifactId],
			foreignColumns: [artifacts.id],
			name: "transform_runs_input_artifact_id_fkey"
		}),
	foreignKey({
			columns: [table.outputArtifactId],
			foreignColumns: [artifacts.id],
			name: "transform_runs_output_artifact_id_fkey"
		}),
	foreignKey({
			columns: [table.transformationId],
			foreignColumns: [transformations.id],
			name: "transform_runs_transformation_id_fkey"
		}),
]);

export const wikiPageVersions = pgTable("wiki_page_versions", {
	pageId: uuid("page_id").notNull(),
	versionNumber: integer("version_number").notNull(),
	content: text(),
	createdById: uuid("created_by_id"),
	updatedById: uuid("updated_by_id"),
	id: uuid().defaultRandom().primaryKey().notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	foreignKey({
			columns: [table.createdById],
			foreignColumns: [users.id],
			name: "wiki_page_versions_created_by_id_fkey"
		}),
	foreignKey({
			columns: [table.pageId],
			foreignColumns: [wikiPages.id],
			name: "wiki_page_versions_page_id_fkey"
		}),
	foreignKey({
			columns: [table.updatedById],
			foreignColumns: [users.id],
			name: "wiki_page_versions_updated_by_id_fkey"
		}),
	unique("wiki_page_versions_page_id_version_number_key").on(table.pageId, table.versionNumber),
]);
