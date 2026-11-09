function main({body, status_code, prepared, player_code}) {
  if (status_code !== 200) throw new Error('模型没有完成行动识别，本轮未结算');
  const response = JSON.parse(body);
  let text = response.choices[0].message.content.trim();
  if (text.startsWith('```')) text = text.replace(/^```[^\n]*\n/, '').replace(/```\s*$/, '').trim();
  const proposal = JSON.parse(text);
  const allowed = ['browse','buy_courage','buy_echo','tell_joke','confess','settle','recall'];
  if (!proposal || !allowed.includes(proposal.action)) throw new Error('模型返回未知行动，本轮未结算');
  const p = JSON.parse(prepared);
  return {body:JSON.stringify({player_code, turn_id:p.turn_id, action:proposal.action})};
}
