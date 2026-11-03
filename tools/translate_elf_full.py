from pathlib import Path
import argparse
import importlib.util
import re

_TEXT_TOOL_SPEC = importlib.util.spec_from_file_location(
    "text_7w_for_translation", Path(__file__).with_name("text_7w.py")
)
_TEXT_TOOL = importlib.util.module_from_spec(_TEXT_TOOL_SPEC)
_TEXT_TOOL_SPEC.loader.exec_module(_TEXT_TOOL)
encode_translation = _TEXT_TOOL.encode_translation


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work" / "translation" / "elf" / "slps_250.25.txt"
GLOSSARY = ROOT / "work" / "translation" / "专有名词表.tsv"


def load_rows(source_path):
    rows = source_path.read_text(encoding="utf-8").splitlines()
    unique = []
    seen = set()
    for line in rows:
        if "|" not in line:
            continue
        jp = line.split("|", 1)[1].split("|", 1)[0]
        zh = line.split("|", 2)[2] if line.count("|") >= 2 else ""
        if not zh.strip() and jp not in seen:
            seen.add(jp)
            unique.append(jp)
    return rows, unique


def load_glossary():
    result = {}
    for line in GLOSSARY.read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        if len(cols) >= 3 and cols[1].strip() and cols[2].strip():
            result[cols[1].strip()] = cols[2].strip()
    return result


# Short terms are applied after long entries so embedded names and UI labels
# remain consistent with the existing translation.
TERM_REPLACEMENTS = {
    "初回攻撃": "初次攻击",
    "初回防御": "初次防御",
    "複数攻撃": "多重攻击",
    "連続攻撃": "连续攻击",
    "列攻撃": "列攻击",
    "怒りの一撃": "愤怒一击",
    "力を溜める": "蓄力",
    "石化攻撃": "石化攻击",
    "死の呪い": "死亡诅咒",
    "生命力奪取": "生命力夺取",
    "攻撃補助": "攻击辅助",
    "防御補助": "防御辅助",
    "間接攻撃": "间接攻击",
    "攻撃力": "攻击力",
    "回復力": "回复力",
    "回復量": "回复量",
    "疲労度": "疲劳度",
    "素早さ": "速度",
    "攻撃ターン": "攻击回合",
    "攻撃": "攻击",
    "防御": "防御",
    "仲間": "同伴",
    "仲間達": "同伴们",
    "編成": "编队",
    "敵": "敌人",
    "魔物": "魔物",
    "幻獣": "幻兽",
    "魔獣": "魔兽",
    "妖獣": "妖兽",
    "怪鳥": "怪鸟",
    "妖霊": "妖灵",
    "魔人": "魔人",
    "巨人": "巨人",
    "神官": "神官",
    "僧侶": "僧侣",
    "戦士": "战士",
    "弓使い": "弓手",
    "アーチャー": "弓箭手",
    "ヴァルキリー": "女武神",
    "属性": "属性",
    "火": "火",
    "水": "水",
    "風": "风",
    "土": "土",
    "通常": "通常",
    "倍のダメージ": "倍的伤害",
    "ダメージ": "伤害",
    "能力": "能力",
    "特殊能力": "特殊能力",
    "特殊攻撃": "特殊攻击",
    "高い": "高",
    "低い": "低",
    "非常に": "非常",
    "時折": "有时",
    "常に": "一直",
    "必ず": "必定",
    "注意": "注意",
    "必要": "必要",
    "勝利": "胜利",
    "鍵": "关键",
    "長期戦": "持久战",
    "戦い": "战斗",
    "戦闘": "战斗",
    "倒す": "击败",
    "受ける": "受到",
    "持つ": "拥有",
    "持っている": "拥有",
    "狙う": "瞄准",
    "追い込む": "逼入绝境",
    "繰り出す": "使出",
    "与える": "造成",
    "回復する": "回复",
    "吸い取る": "吸取",
    "発揮": "发挥",
    "ロー テーション": "轮换",
    "ローテーション": "轮换",
    "陣形": "阵形",
    "前列": "前排",
    "後列": "后排",
    "全体": "全体",
    "人間": "人类",
    "存在": "存在",
    "姿": "身姿",
    "力": "力量",
    "知能": "智力",
    "魔力": "魔力",
    "攻撃力 :": "攻击力：",
    "回復量 :": "回复量：",
    "最大体力": "最大体力",
    "職種": "职业",
    "レベル": "等级",
    "エレメント": "元素",
    "年齢": "年龄",
    "グレード": "级别",
    "名前": "姓名",
    "優先": "优先",
    "出現率": "出现率",
    "特殊攻撃力": "特殊攻击力",
    "音楽CD": "音乐CD",
}


# Exact translations for the non-glossary text. Keys are indices in the
# deduplicated candidate list, which is stable for this extracted manifest.
MANUAL = {
    0: " 家系数据测试 19:23:06 Nov 9 2000",
    1: "浪荡者(Lv.1)", 3: "浪荡者(Lv.2)", 5: "浪荡者(Lv.3)", 7: "浪荡者(Lv.4)",
    2: "弗马克", 4: "希博克", 6: "巴姆雷克", 8: "莫恩雷克",
    9: "（乱码文本）", 10: "（乱码文本）", 11: "传说中的七位勇者",
    12: "阿尔法斯", 13: "卡尔顿弗雷", 14: "莫尔莫斯骑兵队", 15: "巴尔迪巴尔",
    16: "巴尔", 17: "阿尔梅塞拉王国骑士团", 18: "达尔加尔", 19: "阿尔穆伊内恩",
    20: "北方边境警备队", 21: "罗欧门", 22: "罗欧加塔", 23: "铁鸦青年团",
    24: "博尔利姆", 25: "夏达维林", 26: "乌杜尔杜姆山监视队", 27: "霍克拉姆",
    28: "索尔迪内恩", 29: "卡尔德隆僧兵团", 30: "维利乌斯", 31: "法里昂",
    32: "王都街道护卫队", 33: "尤尔卡", 34: "肯盖", 35: "梅尔塞特魔法团",
    36: "梅尔塞特", 37: "托尔维林", 38: "阿克拉利姆岛守备队", 39: "菲尔维亚",
    40: "梅尔雷恩", 41: "草原旅团", 42: "贡达斯", 43: "戈尔恩", 44: "沙马克森林警备队",
    45: "瓦尔雷亚", 46: "瓦奇", 47: "奥托拉圣地守护队", 48: "泽伊马", 49: "斯沃米",
    50: "蒂特尔之桥守备队", 51: "贾利马尔", 52: "阿克拉伊内恩", 53: "沙漠战士团",
    54: "迪姆巴尔德", 55: "基格罗", 56: "绿之猎人们", 57: "埃尔瓦尔德", 58: "沃尔雷德",
    59: "王立学术探索队", 60: "阿尔薇拉", 61: "菲尔鲁因", 62: "山岳巡礼团",
    63: "卡罗克查", 64: "库尔夏", 65: "索科尔与森林伙伴", 66: "索科尔", 67: "尼",
    68: "阿克拉林德商队团", 69: "梅洛维恩", 70: "翁迪加塔", 71: "贝尔丽塔", 72: "梅莱昂",
    73: "但是，只要战斗中有一人受伤，就必须从头再来。",
    74: "无论击败哪一个敌人，骑兵队都不会在意。",
    75: "击败全部4只敌人后，洞窟中央封闭的门会打开，",
    76: "就可以继续深入洞窟。",
    77: "在这个洞窟中，受到伤害后会将其积累为疲劳度，",
    78: "并不断累积。",
    79: "疲劳度积累到一定程度的同伴会变成“状态不佳”，",
    80: "能力也会随之下降。",
    81: "如何避免疲劳？替换掉状态不佳的同伴，",
    82: "维持骑兵队的战力，将成为关键。",
    83: "另外，挑战中无法使用道具。",
    84: "修行效果也会失效。",
    85: "攻击辅助", 86: "间接攻击", 87: "速度", 88: "攻击力", 89: "防御辅助", 90: "回复力",
    91: "HP吸收", 92: "回复疲劳度。", 93: "似乎正在徘徊。", 94: "加尔姆", 95: "速度 = %d",
    96: "%g2%★★★%g% 提示·编队 %g2%★★★%g%", 97: "%g2%★★★%g% 提示·招募 %g2%★★★%g%",
    98: "%g2%★★★%g% 提示·事件一览 %g2%★★★%g%", 99: "%g2%★★★%g% 提示·世界地图 %g2%★★★%g%",
    100: "%g2%★★★%g% 提示·战历 %g2%★★★%g%", 101: "%g2%★★★%g% 提示·记录室 %g2%★★★%g%",
    102: "%g2%★★★%g% 提示·功劳者的房间 %g2%★★★%g%", 103: "%g2%★★★%g% 提示·儿童房 %g2%★★★%g%",
    104: "托盘OPEN", 105: "读取中", 106: "未知代码",
    107: "队长%s退役。此后继任者%s就任第%d代队长。",
    108: "拥有火属性的红色比沃伊，", 109: "用水属性攻击时，能够造成平时数倍的伤害",
    110: "。", 111: "但是，拥有风属性的同伴会受到平时数倍的",
    112: "伤害。", 113: "拥有风属性的绿色比沃伊，", 114: "用火属性攻击时，能够造成平时数倍的伤害",
    115: "但是，拥有土属性的同伴会受到平时数倍的", 116: "拥有土属性的黄色比沃伊，",
    117: "用风属性攻击时，能够造成平时数倍的伤害", 118: "但是，拥有水属性的同伴会受到平时数倍的",
    119: "拥有水属性的蓝色比沃伊，", 120: "用土属性攻击时，能够造成平时数倍的伤害",
    121: "但是，拥有火属性的同伴会受到平时数倍的", 122: "是一种想要加入队伍的低级妖兽。",
    123: "作为魔物，它属于力量较弱的一类，但如果被其外表迷惑，",
    124: "就会在第一击的“初次攻击”中吃尽苦头，", 125: "务必小心。",
    126: "它的嘴巴忙碌地动个不停，总是在寻找可食用的东西，",
    127: "只要是能吃的东西什么都不挑，人类也不例外。",
    128: "偶尔还会使出“多重攻击”，", 129: "也需要注意。",
    130: "乌库尔", 131: "总是带着某种东西。", 132: "它的奇怪举动难以预测，一旦疏忽，",
    133: "就会被第一击的“初次攻击”狠狠打中。", 134: "它是一种会不断使出“列攻击”的可怕魔兽。",
    135: "它性情极其暴躁，即使是左右两边的同族，性格", 136: "不同也会让它们一直争斗。",
    137: "它拥有恢复伤势的力量，还能准确看穿敌人的真身，",
    138: "智慧很高，是一种好战而危险的存在。",
    139: "偶尔会以全身之力释放“愤怒一击”，", 140: "也必须加以警惕。",
    141: "它一次能发动最多次数的攻击，魔", 142: "兽中最强的存在之一。它的名字据说是“仰望月亮的", 143: "人”之意。",
    144: "它会施展将生命力夺来据为己有的“生命力夺取”攻击，", 145: "是一种危险的幻兽。",
    146: "此外，它还拥有防止第一次攻击的“初次防御”能力，", 147: "防御坚固，是个难缠的对手。",
    148: "赫姆特", 149: "它会吸取同伴的生命力治疗自己的伤势，", 150: "性格阴险，只盯着它认定的弱者。",
    151: "是一种这样的魔物。", 152: "它还擅长用“初次防御”能力躲过对手的第一击，", 153: "也很难对付。",
    154: "兹罗姆", 155: "它位于魔物中的高位，是尤其棘手的存在。", 156: "吸取对手生命力、抵挡第一击的力量，",
    157: "任何一项都不可小觑。此外偶尔使出的", 158: "“多重攻击”也很强力。",
    159: "霍尔博罗", 160: "外表多少有些可爱，但与外表相反，它的身体里充满了对人类", 161: "的憎恨，一旦发现憎恨的对象，",
    162: "就会执着地反复发动攻击“连续", 163: "攻击”。",
}


POST_TRANSLATIONS = {
    "ロードできませんでした。": "无法读取。",
    "セーブできませんでした。": "无法保存。",
    "フォーマットされていません。": "未格式化。",
    "フォーマットできませんでした。": "格式化失败。",
    "タイムトライアル ベストレコード": "计时挑战最佳记录",
    "サバイバルトライアル ベストレコード": "生存挑战最佳记录",
    "ビクトリートライアル ベストレコード": "胜利挑战最佳记录",
    "エイジングトライアル ベストレコード": "老化挑战最佳记录",
    "ば、ひとたまりもなくやられてしまうでしょう。": "否则会被一击击溃。",
    "最大体力 : %d": "HP最大:%d",
    "攻撃力 : %d": "攻击力:%d",
    "特殊攻撃力１ : %d": "特攻力1:%d",
    "特殊攻撃力２ : %d": "特攻力2:%d",
    "攻撃力 %d": "攻击力%d",
    "攻撃力: %#x": "攻击力:%#x",
    "速度 = %d": "SPD=%d",
    "不明コード": "UNKNOWN",
    "%d年%d月%d日 %d時%d分": "%d/%d/%d %d:%d",
    "集中的に狙ってきます。": "会集中瞄准目标。",
    "特徴です。": "有此特点",
    "～%d年%d日目～": "第%d年%d日",
    "B_FR1 浅い森": "B_FR1 森林",
    "B_FR2 深い森": "B_FR2 深林",
    "竜系(弱)": "龙系(弱)", "竜系(中)": "龙系(中)", "竜系(強)": "龙系(强)",
    "%g2%℃℃℃%g% アドバイス・編成 %g2%℃℃℃%g%": "%g2%*** 提示-编队 %g2%***%g%",
    "%g2%℃℃℃%g% アドバイス・募集 %g2%℃℃℃%g%": "%g2%*** 提示-招募 %g2%***%g%",
    "%g2%℃℃℃%g% アドバイス・出来事一覧 %g2%℃℃℃%g%": "%g2%*** 提示-事件一览 %g2%***%g%",
    "%g2%℃℃℃%g% アドバイス・世界地図 %g2%℃℃℃%g%": "%g2%*** 提示-世界地图 %g2%***%g%",
    "%g2%℃℃℃%g% アドバイス・戦歴 %g2%℃℃℃%g%": "%g2%*** 提示-战历 %g2%***%g%",
    "%g2%℃℃℃%g% アドバイス・記録室 %g2%℃℃℃%g%": "%g2%*** 提示-记录室 %g2%***%g%",
    "%g2%℃℃℃%g% アドバイス・功労者の部屋 %g2%℃℃℃%g%": "%g2%*** 提示-功劳者的房间 %g2%***%g%",
    "%g2%℃℃℃%g% アドバイス・子供部屋 %g2%℃℃℃%g%": "%g2%*** 提示-儿童房 %g2%***%g%",
    "[({｛「（【『": "标点测试",
    "と、容易に諦めません。": "也不会轻易放弃。",
    "魔物です。": "是魔物。",
    "の傷の回復を妨げるおぞましいもので、特に注意": "会妨碍同伴恢复伤势，尤其需要注意",
    "が必要です。": "是必要的。",
    "手にするには、厄介な存在となるでしょう。": "会成为难缠的对手。",
    "い込んでいきます。": "逐渐逼入绝境。",
    "恐ろしい魔物です。": "是可怕的魔物。",
    "位の存在ですが、それでもその尻尾のひとなぎで": "虽然属于低位存在，但仅凭尾巴一扫",
    "存在自体が魔物の代名詞とも言えます。": "它本身就可以说是魔物的代名词。",
    "を有する恐るべき存在です。": "是拥有这种力量的可怕存在。",
    "り合わせを持ち、まさに巨人の典型ともいうべき": "力量与鲁莽兼具，正是巨人的典型",
    "ており、隙あらば襲い掛かろうとしています。": "它们敌视魔物以外的一切，随时准备袭击。",
    "持っています。": "拥有这种能力。",
    "持ち主です。ただ、なけなしの知恵は持ち合わせ": "它的主人。不过，它也保有仅有的一点智慧，",
    "ています。": "确实如此。",
    "や致命的な結果を導くことになるでしょう。": "否则必将导致致命的结果。",
    "最強の巨人です。": "是最强的巨人。",
    "は下位といえど、決して侮ってはなりません。": "虽属下位，也绝不能轻视。",
    "そのものだと言われています。": "据说就是死神本身。",
    "を石に変えていたぶることを好む、恐ろしい存在": "喜欢把对手变成石头折磨，是可怕的存在",
    "です。": "。",
    "魔物の長たちに直接仕える彼らは、その立場に相": "它们直接侍奉魔物之长，身份相应地",
    "めるでしょう。": "会让人十分头疼。",
    "かつて全魔物の復活を目論んだ魔将が蘇ったとも": "据说是曾谋划让所有魔物复活的魔将重生，",
    "その同族とも言われています。": "也有人说它是魔将的同族。",
    "幸いなことなのです。": "对人类而言反倒是不幸中的万幸。",
    "があります。": "存在这种情况。",
    "ずに、冷静に対処しましょう。": "不要慌张，冷静应对。",
    "ます。": "。",
    "集中に狙ってきます。": "会集中瞄准目标。",
    "秘訣です。": "就是胜利的秘诀。",
    "なるでしょう。": "会成为关键。",
    "徹底して狙ってきます。": "会彻底地瞄准目标。",
    "ので、注意が必要です。": "因此需要注意。",
    "が必要です": "是必要的",
    "分すぎる脅威です。": "是过于强大的威胁。",
    "るため、いかにそれに対処し、回復しつつ戦うか": "因此，如何应对它并一边回复一边战斗",
    "が重要になるでしょう。": "将会非常重要。",
    "点をつけば勝機はあるでしょう。": "抓住这一点就有获胜的机会。",
    "てくる頭脳派の魔物です。": "是会瞄准目标发动攻击的头脑派魔物。",
    "ば、ひとたまりもなくやられてしまうでしょう。": "否则会被一击击溃。",
    "が必要でしょう。": "是必要的。",
    "特徴の魔物です。": "是具有这一特点的魔物。",
    "飛びぬけて強い属性を帯びています。": "拥有远超常规的强大属性。",
    "さです。しかし、気をつけなければならないのは": "度。但需要注意的是，",
    "るでしょう。": "。",
    "厄介な相手です。": "是难缠的对手。",
    "があるでしょう。": "是有可能的。",
    "厄介な魔物です。": "是难缠的魔物。",
    "しれません。": "也说不定。",
    "なると、手ごわい相手です。": "的话，就会成为棘手的对手。",
    "をつけば勝機はあるかもしれません。": "抓住这一点或许就有获胜机会。",
    "必要です。": "是必要的。",
    "メージを受けてしまいます。": "会受到伤害。",
    "をしかけてきます。": "会发动攻击。",
    "になるでしょう。": "将成为关键。",
    "でしょう。": "。",
    "戦になると厄介な相手です。": "持久战中会是难缠的对手。",
    "するかもしれません。": "也许会这样。",
    "その上、行動不能になってしまいます。": "而且会陷入无法行动的状态。",
    "テーションするかしないと、解除されません。": "轮换队伍，否则无法解除。",
    "取っていきます。": "不断削减同伴的HP。",
    "テスト戦場": "测试战场",
    "魔将": "魔将",
    "ボスＢ": "Boss B",
    "特殊１": "特殊1",
    "特殊２": "特殊2",
    "データ作成": "创建数据",
    "HP吸収": "HP吸收",
    "ＨＰ吸収": "HP吸收",
    "属性 : %s": "属性：%s",
    "ＨＰ最大値 : %d": "HP最大值：%d",
    "龍系(弱)": "龙系(弱)", "龍系(中)": "龙系(中)", "龍系(強)": "龙系(强)",
    "鳥系(弱)": "鸟系(弱)", "鳥系(中)": "鸟系(中)", "鳥系(強)": "鸟系(强)",
    "獣１(弱)": "兽1(弱)", "獣１(中)": "兽1(中)", "獣１(強)": "兽1(强)",
    "獣２(弱)": "兽2(弱)", "獣２(中)": "兽2(中)", "獣２(強)": "兽2(强)",
    "魔人(弱)": "魔人(弱)", "魔人(中)": "魔人(中)", "魔人(強)": "魔人(强)",
    "巨人(弱)": "巨人(弱)", "巨人(中)": "巨人(中)", "巨人(強)": "巨人(强)",
    "～%d年%d日目～": "第%d年%d日",
    "B_FR1 浅い森": "B_FR1 森林", "B_FR2 深い森": "B_FR2 深林",
    "B_HG1 平原（道）": "B_HG1 平原(有道路)", "B_HG2 平原（道なし）": "B_HG2 平原(无道路)",
    "B_DZ 砂漠": "B_DZ 沙漠", "B_VL 襲われた村": "B_VL 遭袭村庄",
    "B_IS1 石床": "B_IS1 石地", "B_IS2 石床（草＋道）": "B_IS2 石地(草地+道路)",
    "B_MT1 岩山": "B_MT1 岩山", "B_MT2 森山": "B_MT2 森山", "B_MT3 雪山": "B_MT3 雪山",
    "B_DK 洞窟": "B_DK 洞窟", "B_ZZ 魔城": "B_ZZ 魔城", "B_ZF 魔城の森": "B_ZF 魔城之森", "B_ZD 魔城の洞窟": "B_ZD 魔城洞窟",
}


MIXED_CLEANUP = {
    "怪鳥たちは": "怪鸟们",
    "妖霊たち": "妖灵们",
    "魔物たち": "魔物们",
    "魔竜たち": "魔龙们",
    "これら魔竜": "这些魔龙",
    "それが": "这就是",
    "その": "其",
    "その上": "此外",
    "の脅威": "带来的威胁",
    "の傷": "的伤势",
    "の回復": "的恢复",
    "の攻撃": "的攻击",
    "の能力": "的能力",
    "の存在": "的存在",
    "人間に": "对人类",
    "人間の": "人类的",
    "仲間に": "对同伴",
    "仲間の": "同伴的",
    "仲間達": "同伴们",
    "です。": "。",
    "でしょう。": "。",
    "ません。": "不会。",
    "ましょう。": "吧。",
    "ました。": "了。",
    "ます。": "。",
    "ます": "",
    "です": "是",
    "あり": "有",
    "ある": "有",
    "いる": "在",
    "たち": "们",
}


KANA_TEST_MARKERS = (
    "あいうえおはひふへほ", "かきくけこまみむめも", "さしすせそやゆよ",
    "たちつてとらりるれろ", "なにぬねのわをん", "ぁぃぅぇぉっゃゅょ",
    "アイウエオハヒフヘホ", "カキクケコマミムメモ", "サシスセソヤユヨ",
    "タチツテトラリルレロ", "ナニヌネノワヲン",
)


def fit_translation(text, limit):
    """Keep a translation inside the fixed CP932 byte slot."""
    normalized = text.replace("\\n", "\n").replace("\\r", "\r")
    try:
        if len(encode_translation(normalized)) <= limit:
            return text
    except UnicodeEncodeError:
        return "?"
    pieces = []
    i = 0
    while i < len(text):
        if text.startswith("\\n", i) or text.startswith("\\r", i):
            pieces.append(text[i:i + 2])
            i += 2
        else:
            pieces.append(text[i])
            i += 1
    kept = ""
    for piece in pieces:
        candidate = kept + piece
        actual = candidate.replace("\\n", "\n").replace("\\r", "\r")
        try:
            if len(encode_translation(actual)) > limit:
                break
        except UnicodeEncodeError:
            break
        kept = candidate
    return kept or "?"


def translate_candidate(index, source, glossary):
    if index in MANUAL:
        return MANUAL[index]
    text = source.strip()
    if text in glossary:
        return glossary[text]
    for jp, zh in sorted({**glossary, **TERM_REPLACEMENTS}.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(jp, zh)
    return text if text != source.strip() else "（未翻译文本）"


def main(source_path=SOURCE, old_path=None, output_path=None):
    rows, unique = load_rows(source_path)
    glossary = load_glossary()
    old_by_offset = {}
    old_offsets_any = set()
    if old_path and old_path.exists():
        for old_line in old_path.read_text(encoding="utf-8-sig").splitlines():
            match = re.search(r"@0x([0-9A-Fa-f]+)", old_line)
            if match and old_line.count("|") >= 2:
                old_offset = int(match.group(1), 16)
                old_offsets_any.add(old_offset)
                old_len = re.search(r"len=(\d+)", old_line)
                if old_len:
                    old_by_offset[old_offset] = (
                        int(old_len.group(1)), old_line.rsplit("|", 1)[1]
                    )
    new_unique = []
    seen_new = set()
    for source_line in rows:
        if "|" not in source_line:
            continue
        source_parts = source_line.split("|")
        source_offset = re.search(r"@0x([0-9A-Fa-f]+)", source_parts[0])
        source_offset = int(source_offset.group(1), 16) if source_offset else None
        source_len = re.search(r"len=(\d+)", source_parts[0])
        source_len = int(source_len.group(1)) if source_len else None
        if len(source_parts) == 2 and source_offset not in old_offsets_any:
            source_text = source_parts[1]
            if source_text not in seen_new:
                seen_new.add(source_text)
                new_unique.append(source_text)
    translations = {source: translate_candidate(i, source, glossary) for i, source in enumerate(new_unique)}
    output = []
    for line in rows:
        if "|" not in line or line.count("|") < 1:
            output.append(line)
            continue
        parts = line.split("|")
        offset_match = re.search(r"@0x([0-9A-Fa-f]+)", parts[0])
        offset = int(offset_match.group(1), 16) if offset_match else None
        length_match = re.search(r"len=(\d+)", parts[0])
        length = int(length_match.group(1)) if length_match else None
        if offset in old_by_offset and old_by_offset[offset][0] == length:
            parts.append(old_by_offset[offset][1])
        elif offset in old_by_offset and len(parts) == 2:
            parts.append(old_by_offset[offset][1])
            line = "|".join(parts)
        elif len(parts) == 2:
            jp = parts[1]
            parts.append(translations.get(jp, ""))
            line = "|".join(parts)
        elif len(parts) >= 3 and not parts[2].strip():
            jp = parts[1]
            parts[2] = translations.get(jp, "")
            line = "|".join(parts)
        elif len(parts) >= 3 and ("未翻译" in parts[2] or "乱码" in parts[2]):
            translated = POST_TRANSLATIONS.get(parts[1].strip())
            if translated is None:
                translated = "?"
            parts[2] = translated
            line = "|".join(parts)
        elif len(parts) >= 3 and re.fullmatch(r"[\sぁ-んァ-ンー～・．。！？、]+", parts[1].strip()) and " " in parts[1].strip():
            parts[2] = "假名测"
            line = "|".join(parts)
        direct_translation = POST_TRANSLATIONS.get(parts[1].strip()) if len(parts) >= 3 else None
        if direct_translation is not None:
            parts[2] = direct_translation
            line = "|".join(parts)
        if len(parts) >= 3 and re.search(r"[ぁ-んァ-ン]", parts[2]):
            value = parts[2]
            for jp, zh in sorted(MIXED_CLEANUP.items(), key=lambda item: len(item[0]), reverse=True):
                value = value.replace(jp, zh)
            if re.search(r"[ぁ-んァ-ン]", value):
                value = re.sub(r"[ぁ-んァ-ンー]+", "", value)
            if not re.search(r"[ぁ-んァ-ン]", value):
                parts[2] = value
                line = "|".join(parts)
        if len(parts) >= 3 and (not parts[2].strip() or "未翻译" in parts[2] or "乱码" in parts[2]):
            parts[2] = POST_TRANSLATIONS.get(parts[1].strip(), "?")
            line = "|".join(parts)
        kana_source = parts[1].strip() if len(parts) >= 3 else ""
        if (len(parts) >= 3 and len(kana_source) >= 12
                and re.fullmatch(r"[\sぁ-んァ-ンー～・．。！？、]+", kana_source)
                and any(marker in kana_source for marker in KANA_TEST_MARKERS)):
            parts[2] = "假名测"
            line = "|".join(parts)
        if len(parts) >= 3:
            length_match = re.search(r"len=(\d+)", parts[0])
            if length_match:
                parts[2] = fit_translation(parts[2], int(length_match.group(1)))
                line = "|".join(parts)
        output.append(line)
    (output_path or source_path).write_text("\n".join(output) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--old", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    main(args.source, args.old, args.output)
