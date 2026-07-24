export interface KnowledgeBaseMembership {
  knowledge_base_id: string;
  knowledge_base_name: string;
  role: string;
}

export interface KbConfig {
  relevance_prompt: string;
  entity_types: string[];
  relationship_types: string[];
}

export interface Me {
  id: string;
  email: string;
  name: string | null;
  email_verified: boolean;
  is_admin: boolean;
  knowledge_bases: KnowledgeBaseMembership[];
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface CreatedApiKey {
  id: string;
  name: string;
  prefix: string;
  key: string;
}
