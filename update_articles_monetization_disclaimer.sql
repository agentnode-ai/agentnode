BEGIN;

-- Add "Coming Soon" disclaimer to 3 monetization blog posts
-- These articles promise revenue sharing features that are not yet available

UPDATE blog_posts SET content_html = '<div style="border: 2px solid #f59e0b; background: #fef3c7; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px; color: #92400e;"><strong>Coming Soon:</strong> Revenue sharing and monetization features described in this article are planned but not yet available. AgentNode currently focuses on publishing, verification, and discovery. We will update this article when monetization launches.</div>' || content_html, updated_at = NOW()
WHERE slug = 'how-to-sell-ai-agent-tools-monetize-skills' AND content_html NOT LIKE '%Coming Soon:%';

UPDATE blog_posts SET content_html = '<div style="border: 2px solid #f59e0b; background: #fef3c7; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px; color: #92400e;"><strong>Coming Soon:</strong> Revenue sharing and monetization features described in this article are planned but not yet available. AgentNode currently focuses on publishing, verification, and discovery. We will update this article when monetization launches.</div>' || content_html, updated_at = NOW()
WHERE slug = 'how-much-earn-selling-ai-agent-tools-revenue' AND content_html NOT LIKE '%Coming Soon:%';

UPDATE blog_posts SET content_html = '<div style="border: 2px solid #f59e0b; background: #fef3c7; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px; color: #92400e;"><strong>Coming Soon:</strong> Revenue sharing and monetization features described in this article are planned but not yet available. AgentNode currently focuses on publishing, verification, and discovery. We will update this article when monetization launches.</div>' || content_html, updated_at = NOW()
WHERE slug = 'top-selling-agent-skills-successful-ai-tools' AND content_html NOT LIKE '%Coming Soon:%';

COMMIT;

-- Verify
SELECT slug, LEFT(content_html, 80) as starts_with FROM blog_posts WHERE slug IN ('how-to-sell-ai-agent-tools-monetize-skills', 'how-much-earn-selling-ai-agent-tools-revenue', 'top-selling-agent-skills-successful-ai-tools');
