import { relations } from "drizzle-orm/relations";
import { users, appSettings, orgs, artifacts, orgSettings, transformations, orgUsers, rssFeeds, wikiPages, rssFeedItems, transformRuns, wikiPageVersions } from "./schema";

export const usersRelations = relations(users, ({one, many}) => ({
	user_createdById: one(users, {
		fields: [users.createdById],
		references: [users.id],
		relationName: "users_createdById_users_id"
	}),
	users_createdById: many(users, {
		relationName: "users_createdById_users_id"
	}),
	user_updatedById: one(users, {
		fields: [users.updatedById],
		references: [users.id],
		relationName: "users_updatedById_users_id"
	}),
	users_updatedById: many(users, {
		relationName: "users_updatedById_users_id"
	}),
	appSettings_createdById: many(appSettings, {
		relationName: "appSettings_createdById_users_id"
	}),
	appSettings_updatedById: many(appSettings, {
		relationName: "appSettings_updatedById_users_id"
	}),
	orgs_createdById: many(orgs, {
		relationName: "orgs_createdById_users_id"
	}),
	orgs_updatedById: many(orgs, {
		relationName: "orgs_updatedById_users_id"
	}),
	orgSettings_createdById: many(orgSettings, {
		relationName: "orgSettings_createdById_users_id"
	}),
	orgSettings_updatedById: many(orgSettings, {
		relationName: "orgSettings_updatedById_users_id"
	}),
	transformations_createdById: many(transformations, {
		relationName: "transformations_createdById_users_id"
	}),
	transformations_updatedById: many(transformations, {
		relationName: "transformations_updatedById_users_id"
	}),
	orgUsers_createdById: many(orgUsers, {
		relationName: "orgUsers_createdById_users_id"
	}),
	orgUsers_updatedById: many(orgUsers, {
		relationName: "orgUsers_updatedById_users_id"
	}),
	orgUsers_userId: many(orgUsers, {
		relationName: "orgUsers_userId_users_id"
	}),
	rssFeeds_createdById: many(rssFeeds, {
		relationName: "rssFeeds_createdById_users_id"
	}),
	rssFeeds_updatedById: many(rssFeeds, {
		relationName: "rssFeeds_updatedById_users_id"
	}),
	wikiPages_createdById: many(wikiPages, {
		relationName: "wikiPages_createdById_users_id"
	}),
	wikiPages_updatedById: many(wikiPages, {
		relationName: "wikiPages_updatedById_users_id"
	}),
	wikiPageVersions_createdById: many(wikiPageVersions, {
		relationName: "wikiPageVersions_createdById_users_id"
	}),
	wikiPageVersions_updatedById: many(wikiPageVersions, {
		relationName: "wikiPageVersions_updatedById_users_id"
	}),
}));

export const appSettingsRelations = relations(appSettings, ({one}) => ({
	user_createdById: one(users, {
		fields: [appSettings.createdById],
		references: [users.id],
		relationName: "appSettings_createdById_users_id"
	}),
	user_updatedById: one(users, {
		fields: [appSettings.updatedById],
		references: [users.id],
		relationName: "appSettings_updatedById_users_id"
	}),
}));

export const orgsRelations = relations(orgs, ({one, many}) => ({
	user_createdById: one(users, {
		fields: [orgs.createdById],
		references: [users.id],
		relationName: "orgs_createdById_users_id"
	}),
	user_updatedById: one(users, {
		fields: [orgs.updatedById],
		references: [users.id],
		relationName: "orgs_updatedById_users_id"
	}),
	artifacts: many(artifacts),
	orgSettings: many(orgSettings),
	transformations: many(transformations),
	orgUsers: many(orgUsers),
	rssFeeds: many(rssFeeds),
	wikiPages: many(wikiPages),
}));

export const artifactsRelations = relations(artifacts, ({one, many}) => ({
	org: one(orgs, {
		fields: [artifacts.orgId],
		references: [orgs.id]
	}),
	transformRuns_inputArtifactId: many(transformRuns, {
		relationName: "transformRuns_inputArtifactId_artifacts_id"
	}),
	transformRuns_outputArtifactId: many(transformRuns, {
		relationName: "transformRuns_outputArtifactId_artifacts_id"
	}),
}));

export const orgSettingsRelations = relations(orgSettings, ({one}) => ({
	user_createdById: one(users, {
		fields: [orgSettings.createdById],
		references: [users.id],
		relationName: "orgSettings_createdById_users_id"
	}),
	org: one(orgs, {
		fields: [orgSettings.orgId],
		references: [orgs.id]
	}),
	user_updatedById: one(users, {
		fields: [orgSettings.updatedById],
		references: [users.id],
		relationName: "orgSettings_updatedById_users_id"
	}),
}));

export const transformationsRelations = relations(transformations, ({one, many}) => ({
	user_createdById: one(users, {
		fields: [transformations.createdById],
		references: [users.id],
		relationName: "transformations_createdById_users_id"
	}),
	org: one(orgs, {
		fields: [transformations.orgId],
		references: [orgs.id]
	}),
	user_updatedById: one(users, {
		fields: [transformations.updatedById],
		references: [users.id],
		relationName: "transformations_updatedById_users_id"
	}),
	transformRuns: many(transformRuns),
}));

export const orgUsersRelations = relations(orgUsers, ({one}) => ({
	user_createdById: one(users, {
		fields: [orgUsers.createdById],
		references: [users.id],
		relationName: "orgUsers_createdById_users_id"
	}),
	org: one(orgs, {
		fields: [orgUsers.orgId],
		references: [orgs.id]
	}),
	user_updatedById: one(users, {
		fields: [orgUsers.updatedById],
		references: [users.id],
		relationName: "orgUsers_updatedById_users_id"
	}),
	user_userId: one(users, {
		fields: [orgUsers.userId],
		references: [users.id],
		relationName: "orgUsers_userId_users_id"
	}),
}));

export const rssFeedsRelations = relations(rssFeeds, ({one, many}) => ({
	user_createdById: one(users, {
		fields: [rssFeeds.createdById],
		references: [users.id],
		relationName: "rssFeeds_createdById_users_id"
	}),
	org: one(orgs, {
		fields: [rssFeeds.orgId],
		references: [orgs.id]
	}),
	user_updatedById: one(users, {
		fields: [rssFeeds.updatedById],
		references: [users.id],
		relationName: "rssFeeds_updatedById_users_id"
	}),
	rssFeedItems: many(rssFeedItems),
}));

export const wikiPagesRelations = relations(wikiPages, ({one, many}) => ({
	user_createdById: one(users, {
		fields: [wikiPages.createdById],
		references: [users.id],
		relationName: "wikiPages_createdById_users_id"
	}),
	org: one(orgs, {
		fields: [wikiPages.orgId],
		references: [orgs.id]
	}),
	user_updatedById: one(users, {
		fields: [wikiPages.updatedById],
		references: [users.id],
		relationName: "wikiPages_updatedById_users_id"
	}),
	wikiPageVersions: many(wikiPageVersions),
}));

export const rssFeedItemsRelations = relations(rssFeedItems, ({one}) => ({
	rssFeed: one(rssFeeds, {
		fields: [rssFeedItems.feedId],
		references: [rssFeeds.id]
	}),
}));

export const transformRunsRelations = relations(transformRuns, ({one}) => ({
	artifact_inputArtifactId: one(artifacts, {
		fields: [transformRuns.inputArtifactId],
		references: [artifacts.id],
		relationName: "transformRuns_inputArtifactId_artifacts_id"
	}),
	artifact_outputArtifactId: one(artifacts, {
		fields: [transformRuns.outputArtifactId],
		references: [artifacts.id],
		relationName: "transformRuns_outputArtifactId_artifacts_id"
	}),
	transformation: one(transformations, {
		fields: [transformRuns.transformationId],
		references: [transformations.id]
	}),
}));

export const wikiPageVersionsRelations = relations(wikiPageVersions, ({one}) => ({
	user_createdById: one(users, {
		fields: [wikiPageVersions.createdById],
		references: [users.id],
		relationName: "wikiPageVersions_createdById_users_id"
	}),
	wikiPage: one(wikiPages, {
		fields: [wikiPageVersions.pageId],
		references: [wikiPages.id]
	}),
	user_updatedById: one(users, {
		fields: [wikiPageVersions.updatedById],
		references: [users.id],
		relationName: "wikiPageVersions_updatedById_users_id"
	}),
}));