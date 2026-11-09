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
  const excerpt = Array.from(r.history || '').slice(0,1300).join('').replace(/```/g,'｀｀｀');
  const memory = excerpt ? '本轮从PowerContext读到的回忆片段：\n\n```text\n'+excerpt+'\n```' : '本轮未召回相关旧事。';
  return {answer:'### 🏪 因果便利店\n\n'+dialogue+'\n\n**已确认账单**：'+r.receipt+'\n\n'+r.state_card+'\n\n'+menu+
    '\n\n✅ 本轮真实互动已存档。\n\n<details><summary>翻看老板的记忆账本</summary>\n\n'+memory+'\n\n</details>'};
}
