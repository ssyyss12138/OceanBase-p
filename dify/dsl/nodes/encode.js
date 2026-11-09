function main({player_code, query, turn_id}) {
  if (!/^[A-Za-z0-9_-]{12,80}$/.test(player_code)) {
    throw new Error('存档码应为12—80位英文字母、数字、下划线或连字符');
  }
  if (!query.trim() || Array.from(query).length > 1200) {
    throw new Error('请输入1—1200字的行动');
  }
  if (!/^[a-f0-9-]{36}$/.test(turn_id)) throw new Error('未取得Dify运行标识');
  return {body: JSON.stringify({player_code, query, turn_id})};
}
