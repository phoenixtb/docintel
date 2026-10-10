import { answerEvents, sseBody } from './support/fakeApi';
import { expect, expectNoA11yViolations, test } from './support/fixtures';

test.describe('Chat', () => {
  test('streams an answer with its cited source', async ({ page, api, signIn }) => {
    await signIn('/chat');

    await page.getByRole('textbox', { name: 'Question' }).fill('How many days of annual leave do I get?');
    await page.getByRole('button', { name: 'Send question' }).click();

    await expect(page.getByText('Employees get 25 days of annual leave')).toBeVisible();
    const source = page.getByRole('button', { name: /leave-policy\.pdf/ });
    await expect(source).toBeVisible();
    await expect(source).toContainText('91% relevance');

    await source.click();
    await expect(page.getByText('Employees accrue 25 days of annual leave per year.')).toBeVisible();
  });

  test('sends the question in a new conversation for the signed-in tenant', async ({ page, api, signIn }) => {
    await signIn('/chat');

    await page.getByRole('textbox', { name: 'Question' }).fill('How many days of annual leave do I get?');
    await page.keyboard.press('Enter');
    await expect(page.getByText('Employees get 25 days of annual leave')).toBeVisible();

    expect(api.requestsTo('POST', '/api/v1/conversations')).toHaveLength(1);
    const [query] = api.requestsTo('POST', '/api/v1/query/stream');
    expect(JSON.parse(query.body ?? '{}')).toMatchObject({
      question: 'How many days of annual leave do I get?',
      tenant_id: 'acme',
      conversation_id: 'conv-1',
    });
    await expect(page).toHaveURL('/chat?id=conv-1');
  });

  test('shows an error message when the stream reports an error', async ({ page, api, signIn }) => {
    api.on('POST', '/api/v1/query/stream', () => ({
      contentType: 'text/event-stream',
      body: sseBody([{ metadata: { query_id: 'q-err', cache_hit: false } }, { error: 'LLM unavailable' }]),
    }));
    await signIn('/chat');

    await page.getByRole('textbox', { name: 'Question' }).fill('Anything?');
    await page.getByRole('button', { name: 'Send question' }).click();

    await expect(page.getByText('Sorry, an error occurred. Please try again.')).toBeVisible();
  });

  test('shows an error message when the gateway rejects the query', async ({ page, api, signIn }) => {
    api.on('POST', '/api/v1/query/stream', () => ({
      status: 403,
      json: { error: { code: 'TENANT_CLAIM_MISSING', message: 'Access token has no tenant.' } },
    }));
    await signIn('/chat');

    await page.getByRole('textbox', { name: 'Question' }).fill('Anything?');
    await page.getByRole('button', { name: 'Send question' }).click();

    await expect(page.getByText('Sorry, an error occurred. Please try again.')).toBeVisible();
  });

  test('keeps the send button disabled until a question is typed', async ({ page, signIn }) => {
    await signIn('/chat');

    const send = page.getByRole('button', { name: 'Send question' });
    await expect(send).toBeDisabled();
    await page.getByRole('textbox', { name: 'Question' }).fill('Hello');
    await expect(send).toBeEnabled();
  });

  test('reopens a conversation from the sidebar with its history', async ({ page, api, signIn }) => {
    api.on('GET', '/api/v1/conversations', () => ({
      json: [{ id: 'conv-9', title: 'Leave policy', updated_at: '2026-10-01T09:00:00Z' }],
    }));
    api.on('GET', '/api/v1/conversations/conv-9', () => ({
      json: {
        id: 'conv-9',
        title: 'Leave policy',
        summary_upto_count: 0,
        messages: [
          { id: 'm1', role: 'user', content: 'How long is parental leave?' },
          { id: 'm2', role: 'assistant', content: 'Parental leave is 16 weeks [1].', sources: [] },
        ],
      },
    }));
    await signIn('/chat');

    await page.getByRole('button', { name: /^Leave policy/ }).click();

    await expect(page.getByText('Parental leave is 16 weeks')).toBeVisible();
    await expect(page).toHaveURL('/chat?id=conv-9');
  });

  test('has no WCAG 2.1 AA violations before and after an answer', async ({ page, api, signIn }) => {
    api.queryEvents = answerEvents('Employees get 25 days of annual leave [1].');
    await signIn('/chat');
    await expectNoA11yViolations(page);

    await page.getByRole('textbox', { name: 'Question' }).fill('How many days of annual leave do I get?');
    await page.getByRole('button', { name: 'Send question' }).click();
    await expect(page.getByRole('button', { name: /leave-policy\.pdf/ })).toBeVisible();

    await expectNoA11yViolations(page);
  });
});
