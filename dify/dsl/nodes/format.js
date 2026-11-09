function main({body, status_code, receipt}) {
  const r = JSON.parse(receipt);
  let dialogue;
  try {
    if (status_code !== 200) throw new Error('model unavailable');
    dialogue = JSON.parse(body).choices[0].message.content.trim();
    if (!dialogue) throw new Error('empty dialogue');
    dialogue = Array.from(dialogue).slice(0,1800).join('');
  } catch (error) {
    dialogue = '老板的对讲机暂时失声了。这一轮账单已经确认并保存，下面是真实结果；稍后可以继续互动。';
  }
  let menu = '可继续输入：买勇气汽水 / 买回声糖 / 讲笑话：你的笑话 / 坦白 / 回忆 / 结算。';
  if (r.state.ending) menu = '本局已完成。你仍可询问这段经历；用新的随机存档码开始另一种人生。';
  let memory = '本轮没有翻到相关旧收据。';
  if (r.history) {
    try {
      const raw = r.history.split('BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n')[1]
        .split('\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1')[0];
      const facts = JSON.parse(raw).items.slice(0,2).map(item => {
        const event = JSON.parse(item.content);
        if (typeof event['玩家真实输入'] !== 'string' || typeof event['事实结果'] !== 'string')
          throw new Error('unsupported receipt');
        return '你说：'+Array.from(event['玩家真实输入']).slice(0,300).join('')+
          '\n后来：'+Array.from(event['事实结果']).slice(0,200).join('');
      });
      if (facts.length) memory = '```text\n'+facts.join('\n\n').replace(/```/g,'｀｀｀')+'\n```';
    } catch (error) {
      memory = '旧收据已供老板参考，本页暂时无法展开。';
    }
  }
  return {answer:'### 🏪 因果便利店\n\n'+dialogue+'\n\n**已确认账单**：'+r.receipt+'\n\n'+r.state_card+'\n\n'+menu+
    '\n\n✅ 本轮真实互动已存档。\n\n**老板翻出的旧收据**\n\n'+memory};
}
