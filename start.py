import json
import subprocess
import time
import pickle
import argparse
from typing import Dict, List
from xmlrpc.client import ServerProxy
from subprocess import Popen, PIPE

import majsoul_wrapper as sdk
from majsoul_wrapper import Operation
from majsoul_wrapper.liqi import LiqiProto

class CardRecorder:
    # 记录牌的操作历史mjai json
    def __init__(self):
        self.clear()
        
    def clear(self):
        self.mjai = []
        self.mjai.append({
            "type": "start_game",
        })
    
    bakazemap = ['E', 'S', 'W', 'N']
    
    def start(self, bakaze, dora_marker, kyoku, honba, kyotaku, oya, scores, tehais):
        
        self.mjai.append({
            "type": "start_kyoku",
            "bakaze": self.bakazemap[bakaze],
            "dora_marker": dora_marker,
            "kyoku": kyoku,
            "honba": honba,
            "kyotaku": kyotaku,
            "oya": oya,
            "scores": scores,
            "tehais": tehais
        })
        
    def add(self, type, actor=None, pai=None, target=None, consumed=None, tsumogiri=None, dora_marker=None, deltas=None, ura_markers=None):
        actor = str(actor)
        if type == 'tsumo':
            self.mjai.append({
                "type": type,
                "actor": actor,
                "pai": pai
            })
        elif type == 'dahai':    
            self.mjai.append({
                "type": type,
                "actor": actor,
                "pai": pai,
                "tsumogiri": tsumogiri
            })
        elif type == 'chi':
            self.mjai.append({
                "type": type,
                "actor": actor,
                "target": target,
                "pai": pai,
                "consumed": consumed,
            })
        elif type == 'pon':
            self.mjai.append({
                "type": type,
                "actor": actor,
                "target": target,
                "pai": pai,
                "consumed": consumed,
            })
        elif type == 'daiminkan':
            self.mjai.append({
                "type": type,
                "actor": actor,
                "target": target,
                "pai": pai,
                "consumed": consumed,
            })
        elif type == 'kakan':
            self.mjai.append({
                "type": type,
                "actor": actor,
                "pai": pai,
                "consumed": consumed,
            })
        elif type == 'ankan':
            self.mjai.append({
                "type": type,
                "actor": actor,
                "consumed": consumed,
            })
        elif type == 'dora':
            self.mjai.append({
                "type": type,
                "dora_marker": dora_marker,
            })
        elif type == 'reach':
            self.mjai.append({
                "type": type,
                "actor": actor,
            })
        elif type == 'reach_accepted':
            self.mjai.append({
                "type": type,
                "actor": actor,
            })
        elif type == 'hora':
            self.mjai.append({
                "type": type,
                "actor": actor,
                "target": target,
                "deltas": deltas,
                "ura_markers": ura_markers,
            })
        elif type == 'ryukyoku':
            self.mjai.append({
                "deltas": deltas,
            })
        elif type == 'end_kyoku':
            self.mjai.append({
                "type": type
            })
            
    def get(self):
        return "\n".join(list(map(lambda x : json.dumps(x), self.mjai)))
    

class AIWrapper(sdk.GUIInterface, sdk.MajsoulHandler):
    # TenHouAI <-> AI_Wrapper <-> Majsoul Interface

    def __init__(self):
        super().__init__()
        self.AI_socket = None
        # 与Majsoul的通信
        self.majsoul_server = ServerProxy(
            "http://127.0.0.1:37247")   # 初始化RPC服务器
        self.liqiProto = sdk.LiqiProto()
        # 牌号转换
        self.cardRecorder = CardRecorder()

    def init(self):
        self.majsoul_history_msg = []   # websocket flow_msg
        self.majsoul_msg_p = 0  # 当前准备解析的消息下标
        self.liqiProto.init()
        self.hai = []                   # 我当前手牌的tile136编号(和AI一致)
        self.isLiqi = False             # 当前是否处于立直状态
        self.wait_a_moment = False      # 下次操作是否需要额外等待
        self.lastSendTime = time.time()  # 防止操作过快
        self.pengInfo = dict()          # 记录当前碰的信息，以维护加杠时的一致性
        self.lastOperation = None       # 用于判断吃碰是否需要二次选择
        self.playerid = -1              # 玩家id
    
    def isPlaying(self) -> bool:
        # 从majsoul websocket中获取数据，并判断数据流是否为对局中
        n = self.majsoul_server.get_len()
        liqiProto = sdk.LiqiProto()
        if n == 0:
            return False
        flow = pickle.loads(self.majsoul_server.get_items(0, min(100, n)).data)
        for flow_msg in flow:
            result = liqiProto.parse(flow_msg)
            if result.get('method', '') == '.lq.FastTest.authGame':
                return True
        return False
    
    def recvFromMajsoul(self):
        # 从majsoul websocket中获取数据，并尝试解析执行。
        # 如果未达到要求无法执行则锁定self.majsoul_msg_p直到下一次尝试。
        n = self.majsoul_server.get_len()
        l = len(self.majsoul_history_msg)
        if l < n:
            flow = pickle.loads(self.majsoul_server.get_items(l, n).data)
            self.majsoul_history_msg = self.majsoul_history_msg+flow
            pickle.dump(self.majsoul_history_msg, open(
                'websocket_frames.pkl', 'wb'))
        if self.majsoul_msg_p < n:
            flow_msg = self.majsoul_history_msg[self.majsoul_msg_p]
            result = self.liqiProto.parse(flow_msg)
            failed = self.parse(result)
            if not failed:
                self.majsoul_msg_p += 1
    
    
    #------------------MajsoulHandler------------------
    
    def authGame(self, accountId: int, seatList: List[int]):
        super().authGame(accountId, seatList)
        
        self.accountId = accountId
        self.seatList = seatList
        self.userid = {
            seatList[0]: 0,
            seatList[1]: 1,
            seatList[2]: 2,
            seatList[3]: 3,
        }
        
    
    def subcard(self, card: str) -> str:
        if card[0] == '1':
            return '9'+card[1:]
        elif card[0] >= '2' and card[0] <= '9':
            return str(int(card[0]) - 1) + card[1:]
        # E | S | W | N
        elif card[0] == 'E':
            return 'N'
        elif card[0] == 'S':
            return 'E'
        elif card[0] == 'W':
            return 'S'
        elif card[0] == 'N':
            return 'W'
        # F 发 | C 中 | P 白
        elif card[0] == 'P':
            return 'C'
        elif card[0] == 'C':
            return 'F'
        elif card[0] == 'F':
            return 'P'
        else:
            return card        

    cardmap = ['', 'E', 'S', 'W', 'N', 'P', 'F', 'C']
    
    def transcard(self, card: str) -> str:
        print(card)
        if card[0] == '0':
            return '5'+card[1] + 'r'
        elif card[1] == 'z':
            return self.cardmap[int(card[0])]
        else:
            return card
    
    def transcard2majsoul(self, card: str) -> str:
        if len(card) >= 3:
            return '0' + card[1]
        elif len(card) == 1:
            return 'z' + str(self.cardmap.index(card))
        else:
            return card
            
    
    def newRound(self, chang: int, ju: int, ben: int, liqibang: int, tiles: List[str], scores: List[int], leftTileCount: int, doras: List[str]):
        """
        chang:当前的场风，0~3:东南西北
        ju:当前第几局(0:1局,3:4局，连庄不变)
        liqibang:流局立直棒数量(画面左上角一个红点的棒)
        ben:连装棒数量(画面左上角八个黑点的棒)
        tiles:我的初始手牌
        scores:当前场上四个玩家的剩余分数(从东家开始顺序)
        leftTileCount:剩余牌数
        doras:宝牌列表
        """
        super().newRound(chang, ju, ben, liqibang, tiles, scores, leftTileCount, doras)
        
        self.isLiqi = False
        # self.pengInfo.clear()
        
        tehais = [list(map(lambda x:self.transcard(x), tiles)) if i == self.mySeat else ['?' for i in range(13)] for i in range(4)]
        
        self.cardRecorder.start(chang, self.subcard(self.transcard(doras[0])), ju, ben, liqibang, ju, scores, tehais)
        
        if self.mySeat == ju:
            self.wait_a_moment = True
            self.recv()
        
    def newDora(self, dora: str):
        super().newDora(dora)
    
        self.cardRecorder.add('dora', dora_marker=self.subcard(self.transcard(dora)))
    
    def dealTile(self, seat: int, leftTileCount: int, liqi: Dict):
        super().dealTile(seat, leftTileCount, liqi)
        
        if liqi:
            self.cardRecorder.add('reach_accepted', actor=liqi.get('seat', 0))
        
        self.cardRecorder.add('tsumo', actor=seat, pai='?')
        
    def iDealTile(self, seat: int, tile: str, leftTileCount: int, liqi: Dict, operation: Dict):
        super().iDealTile(seat, tile, leftTileCount, liqi, operation)
        if liqi:
            self.cardRecorder.add('reach_accepted', actor=self.userid[liqi.get('seat', 0)])
        if operation != None:
            opList = operation.get('operationList', [])
            canJiaGang = any(
                op['type'] == Operation.JiaGang.value for op in opList)
            canLiqi = any(op['type'] == Operation.Liqi.value for op in opList)
            canZimo = any(op['type'] == Operation.Zimo.value for op in opList)
            canHu = any(op['type'] == Operation.Hu.value for op in opList)
            if canHu or canZimo or canLiqi or canJiaGang:
                self.recv()
        
        self.cardRecorder.add('tsumo', actor=seat, pai=self.transcard(tile))
        self.recv()
    
    def discardTile(self, seat: int, tile: str, moqie: bool, isLiqi: bool, operation):
        super().discardTile(seat, tile, moqie, isLiqi, operation)
        if isLiqi:
            self.cardRecorder.add('reach', actor=self.userid[seat])
        
        self.lastOperation = operation
        
        if operation != None:
            assert(operation.get('seat', 0) == self.mySeat)
            opList = operation.get('operationList', [])
            canChi = any(op['type'] == Operation.Chi.value for op in opList)
            canPeng = any(op['type'] == Operation.Peng.value for op in opList)
            canGang = any(
                op['type'] == Operation.MingGang.value for op in opList)
            canHu = any(op['type'] == Operation.Hu.value for op in opList)
            if canHu or canGang or canPeng or canChi:
                self.recv()
        
        
        self.cardRecorder.add('dahai', actor=seat, pai=self.transcard(tile), tsumogiri=moqie)
        

    def chiPengGang(self, type_: int, seat: int, tiles: List[str], froms: List[int], tileStates: List[int]):
        super().chiPengGang(type_, seat, tiles, froms, tileStates)
        # if seat == self.mySeat:
        #     return
        
        target = self.userid[froms[-1]] if len(froms) > 0 else None
        
        pai = self.transcard(tiles[froms.index(seat)])
        
        consumed = [self.transcard(tiles[i]) if froms[i] != seat else None for i in range(len(tiles))]
        
        type = map(lambda x : 'chi' if x == 0 else 'pon' if x == 1 else 'daiminkan', type_)
        
        self.cardRecorder.add(type, actor=self.userid[seat], target=target, pai=pai, consumed=consumed)
        
        if seat == self.mySeat:
            self.recv()
    
    def anGangAddGang(self, type_: int, seat: int, tiles: str):
        super().anGangAddGang(type_, seat, tiles)
        
        if type_ == 2:
            if tiles[0] == '5':
                self.cardRecorder.add('kakan', actor=self.userid[seat], pai=tiles, consumed=[self.transcard(tiles), self.transcard(tiles), self.transcard('0'+tiles[1])])
            elif tiles[0] == '0':
                self.cardRecorder.add('kakan', actor=self.userid[seat], pai=self.transcard(tiles), consumed=[self.transcard('5' + tiles[1]), self.transcard('5' + tiles[1]), self.transcard('5' + tiles[1])])
            else:
                self.cardRecorder.add('kakan', actor=self.userid[seat], pai=self.transcard(tiles), consumed=[self.transcard(tiles) for i in range(3)])
        elif type_ == 3:
            tile4 = [tiles.replace('0', '5') for i in range(4)]
            if tiles[0] in '05':
                tile4[0] = '0' + tiles[1]
            self.cardRecorder.add('ankan', actor=self.userid[seat], consumed=[self.transcard(tiles) for tiles in tile4])
        else:
            raise Exception('unknow type')
            
        if seat == self.mySeat:
            self.recv()
    
    def hule(self, hand: List[str], huTile: str, seat: int, zimo: bool, liqi: bool, doras: List[str], liDoras: List[str], fan: int, fu: int, oldScores: List[int], deltaScores: List[int], newScores: List[int]):
        super().hule(hand, huTile, seat, zimo, liqi, doras, liDoras, fan, fu, oldScores, deltaScores, newScores)
        
        self.cardRecorder.add('hora', actor=self.userid[seat], target='?', deltas=deltaScores, ura_markers=[self.subcard(self.transcard(dora)) for dora in liDoras])
    
    def liuju(self, liqi: bool, oldScores: List[int], deltaScores: List[int], newScores: List[int]):
        super().liuju(liqi, oldScores, deltaScores, newScores)
        
        self.cardRecorder.add('ryukyoku', deltas=deltaScores)
        
    def specialLiuju(self):
        super().specialLiuju()
        
        self.cardRecorder.add('ryukyoku', deltas=[0, 0, 0, 0])
        
    #---------------------end------------------------
    
    def wait_for_a_while(self, delay=2.0):
        # 如果读秒不足delay则强行等待一会儿
        dt = time.time()-self.lastSendTime
        if dt < delay:
            time.sleep(delay-dt)
        
    def recv(self):
        # 发送并接受数据
        p = Popen('system.exe mjai_stdin ' + str(self.mySeat), shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE, cwd='akochan')

        p.stdin.write(self.cardRecorder.get().encode('utf-8'))
        p.stdin.flush()
        p.stdin.close()
        p.wait()
        move = p.stdout.readline().decode('utf-8')
        p.stdout.close()
        p.stderr.close()
        json_move = json.loads(move)[0]
        self.lastOperation = json_move
        self.runOperation(json_move)
    
    def runOperation(self, json_move):
        if self.wait_a_moment:
            self.wait_a_moment = False
            time.sleep(2)
        self.wait_for_a_while()
        # 执行操作
        if json_move['type'] == 'dahai':
            if not self.isLiqi:
                self.actionDiscardTile(self.transcard2majsoul(json_move['pai']))
        elif json_move['type'] == 'none':
            if not self.isLiqi:
                self.forceTiaoGuo()
        elif json_move['type'] == 'chi':
            if not self.isLiqi:
                tile1 = self.transcard2majsoul(json_move['consumed'][0])
                tile2 = self.transcard2majsoul(json_move['consumed'][1])
                self.actionChiPengGang(sdk.Operation.Chi,[self.transcard2majsoul(pai) for pai in json_move['consumed']]) # 第二个参数p用没有
                if self.lastOperation != None:
                    opList = self.lastOperation.get('operationList', [])
                    opList = [op for op in opList if op['type']
                            == Operation.Chi.value]
                    assert(len(opList) == 1)
                    op = opList[0]
                    combination = op['combination']
                    # e.g. combination = ['4s|0s', '4s|5s']
                    if len(combination) > 1:
                        # 需要二次选择
                        combination = [tuple(sorted(c.split('|')))
                                    for c in combination]
                        AI_combination = tuple(sorted([tile1, tile2]))
                        assert(AI_combination in combination)
                        # 如果有包含红包牌的同构吃但AI犯蠢没选，强制改为吃红包牌
                        oc = tuple(sorted([i.replace('5', '0')
                                        for i in AI_combination]))
                        if oc in combination:
                            AI_combination = oc
                        print('clickCandidateMeld AI_combination', AI_combination)
                        time.sleep(1)
                        self.clickCandidateMeld(AI_combination)
        elif json_move['type'] == 'pon':
            if not self.isLiqi:
                self.actionChiPengGang(sdk.Operation.Peng,[])
        elif json_move['type'] == 'daiminkan':
            if not self.isLiqi:
                self.actionChiPengGang(sdk.Operation.MingGang,[])
        elif json_move['type'] == 'ankan':
            if not self.isLiqi:
                self.actionChiPengGang(sdk.Operation.MingGang,[])
        elif json_move['type'] == 'kakan':
            if not self.isLiqi:
                self.actionChiPengGang(sdk.Operation.JiaGang,[])
        elif json_move['type'] == 'reach':
            if not self.isLiqi:
                self.actionLiqi()
                self.cardRecorder.add('reach', actor=self.mySeat)
                self.recv()
        elif json_move['type'] == 'reach_accepted':
            self.isLiqi = True
        elif json_move['type'] == 'hora':
            if json_move['actor'] == json_move['target']:
                self.actionZimo()
            else:
                self.actionHu()
                
def MainLoop(level=None):
    # 循环进行段位场对局，level=0~4表示铜/银/金/玉/王之间，None需手动开始游戏
    # calibrate browser position
    aiWrapper = AIWrapper()
    print('waiting to calibrate the browser location')
    while not aiWrapper.calibrateMenu():
        print('  majsoul menu not found, calibrate again')
        time.sleep(3)

    while True:
        # create AI
        aiWrapper.init()

        if level != None:
            aiWrapper.actionBeginGame(level)

        print('waiting for the game to start')
        while not aiWrapper.isPlaying():
            time.sleep(3)

        while True:
            time.sleep(1)
            aiWrapper.recvFromMajsoul()
            if aiWrapper.isEnd:
                aiWrapper.actionReturnToMenu()
                break


def replayWebSocket(filename='ws_dump.pkl'):
    # 回放历史websocket报文，按顺序交由handler.parse
    handler = AIWrapper()
    history_msg = pickle.load(open(filename, 'rb'))
    liqi = LiqiProto()
    for flow_msg in history_msg:
        result = liqi.parse(flow_msg)
        handler.parse(result)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="MajsoulAI")
    parser.add_argument('-l', '--level', default=None)
    parser.add_argument('-t', '--test', default=None)
    args = parser.parse_args()
    if args.test != None:
        replayWebSocket(args.test)
    else:
        level = None if args.level == None else int(args.level)
        MainLoop(level=level)
    