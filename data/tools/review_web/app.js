'use strict';
// 仅用于审核界面的中文释义；英文标签、来源文本和训练数据均不改写。
const reviewZh={
 'Locomotion':'移动动作','Baseline':'基础动作','climbing box':'上 / 下箱','standing':'站立姿态','neutral':'自然风格','50cm box':'50厘米箱体',
 'Basic Locomotion Styles':'基础移动 · 风格动作','BS neutral':'自然风格（BS neutral）','Advanced Locomotion':'复杂移动动作','Basic Locomotion Neutral':'基础移动 · 自然风格',
 'walking':'行走','crouching':'蹲姿','crouching idle':'蹲姿保持','turning':'转身','jumping':'跳跃','jumping, turning':'跳跃、转身','jogging':'慢跑','standing idle':'站立保持','vaulting':'撑越障碍',
 'walking, crouching, transition':'行走、蹲姿、姿态过渡','crouching, leaning, standing':'蹲姿、倚靠、站立','standing, leaning, crouching':'站立、倚靠、蹲姿','wall':'墙体',
 'get down from 50 cm box':'从50厘米箱体下来',
 'individual steps off a small box using their hands for support if needed':'从小箱体上迈下来，需要时用手支撑。',
 'character descends from a 50 cm high box, using the right or both hands for support as needed':'从50厘米高的箱体下来，按需用右手或双手支撑。',
 'person steps down from a 50 cm box using their hands for support if needed':'从50厘米箱体上迈下来，需要时用手支撑。',
 'A person is smoothly getting down from a box of height 50 cm.':'平稳地从50厘米高的箱体下来。',
 'start right foot, end left foot, single movement, get down from box':'右脚起步，左脚结束；单次动作，从箱体下来。',
 'A person is getting down from a box of height 50 cm and stands on the ground.':'从50厘米高的箱体下来，站在地面上。',
 'get up onto 50 cm box':'登上50厘米箱体',
 'step up on a box using hands for support, starting with right foot and ending with left':'用手支撑登上箱体，右脚起步、左脚结束。',
 'person uses their right or both hands for support to get up onto a 50 cm box':'用右手或双手支撑，登上50厘米箱体。',
 'person gets up onto a 50 cm box using their right or both hands for support':'用右手或双手支撑，登上50厘米箱体。',
 'A person standing on the ground gets up onto a raised platform and stands idle, facing forward.':'从地面登上高台，面向前方站立保持。',
 'start right foot, end left foot, single movement, get up on box':'右脚起步，左脚结束；单次动作，登上箱体。',
 'A person standing on the ground steps up on a raised platform and stands still, facing forward.':'从地面迈上高台，面向前方站定。',
 'A person standing upright swiftly gets up onto a 50 cm tall box.':'从直立姿态迅速登上50厘米高的箱体。',
 'A person climbs up and gets onto a 50 cm tall box.':'向上攀登，登上50厘米高的箱体。',
 'A person is standing upright on a 50 cm tall box.':'在50厘米高的箱体上直立站着。',
 'individual steps off a small box':'从小箱体上迈下来。',
 'character descends from a 50 cm high box':'从50厘米高的箱体下来。',
 'person steps down from a 50 cm box':'从50厘米箱体上迈下来。',
 'A person steps onto a 50 cm high box and stands upright.':'迈上50厘米高的箱体，直立站定。',
 'The person lifts the right leg and places it onto the 50 cm high box.':'抬起右腿，将右脚放到50厘米高的箱体上。',
 'The person lifts their left leg, steps onto the 50 cm box, and stands in an upright posture.':'抬起左腿，登上50厘米箱体，然后直立站定。',
 'A person standing idle and upright on a 50 cm high box gently steps down forward to stand idle again.':'从50厘米高箱体上的直立姿态，轻缓地向前迈下，再站立保持。',
 'A person gently steps down forward from a 50 cm high box.':'从50厘米高的箱体轻缓地向前迈下来。',
 'A person straightens up and stands in an upright posture.':'身体伸直，恢复直立站姿。',
 'crouching facing forward directly to the right':'保持面向前方，以蹲姿向正右侧移动。',
 'crouch and creep directly to the right':'蹲低身体，向正右侧缓慢移动。',
 'person moves sideways to the right in a crouching position':'保持蹲姿，向右侧移动。',
 'a crouching person moving directly to right with left foot crossing back':'保持蹲姿向正右侧移动，左脚从后方交叉。',
 'A crouched person walks sideways to their right at a steady pace.':'保持蹲姿，匀速向右侧行走。',
 'move sideways, left foot back crossing, talent crouching, walking in crouch':'向侧面移动，左脚向后交叉；表演者保持蹲姿行走。',
 'crouch position right leg forward':'右腿在前的蹲姿',
 'character with right foot forward assumes a crouching position leaning on the left foot':'右脚在前，重心偏左脚，保持蹲姿。',
 'stand in a crouching position with right foot forward':'右脚在前，保持蹲姿。',
 'maintain a crouching position with the right leg slightly extended forward':'右腿略向前伸，保持蹲姿。',
 'A crouched person is standing in place with their right leg ahead of the left.':'保持蹲姿停在原地，右腿位于左腿前方。',
 'right foot slightly forward, body weight on left foot, talent crouching':'右脚略向前，体重落在左脚；表演者保持蹲姿。',
 'turning back over right shoulder while crouching':'蹲姿向右肩后方转身',
 'character crouches and turns back over the right shoulder':'蹲低身体，并向右肩后方转身。',
 'character makes a 180 degree turn over the right shoulder while crouching':'保持蹲姿，向右肩后方转身180度。',
 'complete a turn back over the right shoulder while keeping a crouch':'保持蹲姿，完成向右肩后方的转身。',
 'A person turns back over the right shoulder in a couching stance.':'以蹲姿向右肩后方转身。（原文 couching 疑为 crouching 的拼写问题。）',
 'start right foot, turn right 180 degrees over right shoulder, 3 steps, talent crouching, walking in crouch':'右脚起步，向右肩后方右转180度，三步完成；表演者保持蹲姿行走。',
 'A crouching person turns backwards over the right shoulder.':'保持蹲姿，向右肩后方转身。',
 'A person sways slightly in a crouching posture.':'保持蹲姿，身体轻微晃动。',
 'jumping high':'高跳',
 'high jump over a moving pole in one place':'原地高跳，越过移动中的杆。',
 'making high jump over a moving pole stationary':'保持原地进行高跳，越过移动中的杆。',
 'performer makes high jump in one place over a moving pole':'表演者原地高跳，越过移动中的杆。',
 'A person energetically performs a jump and then stands idle.':'用力跳跃，随后站立保持。',
 'jump high over moving pole, jump high in one place':'高跳越过移动杆；原地高跳。',
 'jump backwards, finish with right leg':'向后跳，右腿结束动作',
 'jumping back, landing on the right foot':'向后跳，右脚落地。',
 'jumping backwards and landing on the right leg':'向后跳，并以右腿落地。',
 'a jump backwards led by the left foot, while constantly looking forwards':'左脚领动向后跳，始终看向前方。',
 'A person quickly jumps backward, leading with the left leg.':'左腿领动，快速向后跳。',
 'jumping back, left foot first, head looking forward':'向后跳，左脚先动，头部看向前方。',
 'A person is standing still in an upright posture.':'保持直立，静止站立。',
 'A person jumps backwards, leading with the left foot.':'左脚领动，向后跳。',
 'A person is standing upright in a narrow stance.':'双脚间距较窄，直立站着。',
 'jump forward':'向前跳',
 'jumping forward from a standing position':'从站姿向前跳。',
 'make a small forward leap':'小幅向前跳。',
 'person makes a small jump as they move forward':'向前移动时做一次小幅跳跃。',
 'A person is jumping forward with their right foot, taking the leap.':'右脚发起，向前跳跃。',
 'start right foot, move forward, small jump':'右脚起步，向前移动，小幅跳跃。',
 'The person swiftly takes a jump forward with their right foot.':'右脚发起，迅速向前跳跃。',
 'jump right':'向右跳',
 'a short jump to the right from a standing position':'从站姿小幅向右跳。',
 'jump towards the right with a small hop':'小幅跳向右侧。',
 'person takes a small jump sideward starting with the right foot':'右脚先动，做小幅侧向跳跃。',
 'A person makes a quick horizontal jump toward their right side.':'快速向自身右侧跳跃。',
 'start right foot, move sideways, small jump':'右脚起步，向侧面移动，小幅跳跃。',
 'jump backwards':'向后跳',
 'character performs a small jump while moving backward':'向后移动时做一次小幅跳跃。',
 'begin by moving the right foot, then jump backward':'先移动右脚，然后向后跳。',
 'person takes a small jump backward':'小幅向后跳。',
 'A person standing idle takes a small hop backward.':'从站立保持姿态做一次小幅后跳。',
 'start right foot, move backward, small jump':'右脚起步，向后移动，小幅跳跃。',
 'A person jumps backward while facing forward.':'面向前方，向后跳。',
 'The person facing forward jumps backwards.':'面向前方，向后跳。',
 'The person is standing while facing forward.':'面向前方站立。',
 'A person jumps forward, pauses briefly in a standing position, then begins to walk backward while turning to their right.':'先向前跳，短暂站立后，开始后退行走并向右转。',
 'A person jumps forward from a standing position.':'从站姿向前跳。',
 'A person is standing straight and slightly sways their arms.':'直立站着，双臂轻微摆动。',
 'A person initiates walking backward while looking over their right shoulder.':'开始后退行走，同时向右肩后方看。',
 'A person continues walking backward and turns right.':'继续后退行走，并向右转。',
 'jump back left diagonal':'向左后斜跳',
 'character jumps back on the left diagonal, with a head looking forward':'向左后斜跳，头部看向前方。',
 'person jumps back diagonally to the left while keeping their gaze forward':'向左后斜跳，视线保持朝前。',
 'person leaps backwards diagonally to the left, leading with the left foot and keeping their head forward':'左脚领动，向左后斜跳，头部保持朝前。',
 'A person quickly hops towards the back left diagonal direction.':'快速向左后斜向跳跃。',
 'jumping back left diagonal, left foot first, head looking forward':'向左后斜跳，左脚先动，头部看向前方。',
 'jogging clockwise in an arc':'沿顺时针弧线慢跑',
 'a jog in a clockwise circle':'沿顺时针圆形路线慢跑。',
 'jogging clockwise in a circular path':'沿圆形路径顺时针慢跑。',
 'person jogs around in a clockwise circle':'绕圆形路线顺时针慢跑。',
 'A person is jogging clockwise.':'沿顺时针方向慢跑。',
 'move forward in a circle, clockwise, jog':'沿圆形路线向前移动；顺时针慢跑。',
 'start jog to the right':'向右起步慢跑',
 'a jog starts with the right foot, moving slightly to the right':'右脚起步，略向右侧开始慢跑。',
 'person starts with their right foot and moves sideways, slowly jogging':'右脚起步，向侧面慢跑。',
 'character begins jogging to their right, leading with their right foot and pulling their left foot behind them':'右脚领动向右慢跑，左脚跟到身后。',
 'A person swiftly jogs sideways to their right at a normal pace with long strides.':'以较大步幅、正常节奏快速向右侧慢跑。',
 'start right foot, move sideways, left foot back crossing, jog':'右脚起步，向侧面移动，左脚向后交叉，慢跑。',
 'A person standing upright swiftly starts to jog sideways to their right.':'从直立姿态迅速向右侧起步慢跑。',
 'A person in a jogging stance jogs swiftly jogs sideways to their right at a normal pace with long strides.':'保持慢跑姿态，以较大步幅、正常节奏快速向右侧慢跑。',
 'stop jog forward right diagonally':'向右前斜跑后停止',
 'jog diagonally to the right before stopping on the left foot':'向右斜向慢跑，随后用左脚停下。',
 'a halt to a forward right jog with a diagonal step by the left foot':'向右前方慢跑，以左脚斜向迈步结束并停下。',
 'figure ceases jogging and begins moving diagonally to the right':'停止慢跑，开始向右斜向移动。',
 'A person jogs forward diagonally to the right and finally comes to a stop to stand idle.':'向右前斜向慢跑，最后停下并站立保持。',
 'end left foot, move forward diagonally, left foot front crossing, jog':'左脚结束动作；向前斜向移动，左脚向前交叉，慢跑。',
 'A person jogs forward diagonally to the right twice.':'向右前斜向慢跑两次。',
 'standing with hands on back position':'双手背在身后站立',
 'someone is standing with their hands placed behind their back, right hand closer to the body':'双手背在身后站立，右手更靠近身体。',
 'character stands with their hands clasped behind their back':'双手在背后相握，站立保持。',
 'someone stands upright with their hands held behind their back':'双手放在身后，直立站着。',
 'A person is standing still with their hands clasped behind their back.':'双手在背后相握，静止站立。',
 'hands behind back, right hand closer to body':'双手在背后，右手更靠近身体。',
 'A person is standing still, facing forward, with their hands clasped behind their back.':'面向前方，双手在背后相握，静止站立。',
 'neutral position with right leg forward':'右腿在前的自然站姿',
 'character stands in place with the right foot slightly forward, leaning on the left foot':'原地站立，右脚略向前，重心偏左脚。',
 'stand in a neutral position with right foot slightly forward':'右脚略向前，保持自然站姿。',
 'show a neutral standing posture with the right leg slightly forward':'右腿略向前，呈自然站姿。',
 'A person stands idle with a subtle tilt in the hips, facing forward.':'面向前方站立保持，骨盆略微倾斜。',
 'right foot slightly forward, body weight on left foot, standing in one place':'右脚略向前，体重落在左脚，原地站立。',
 'A person stands idle with a subtle tilt in the hips and the arms to the side, facing forward.':'面向前方站立保持，骨盆略倾斜，双臂放在身体两侧。',
 'start from crouching while supporting with hand to standing':'从用手支撑的蹲姿开始起身站立',
 'a sequence of standing from a crouch position, using a wall for support':'借助墙体支撑，从蹲姿起身站立。',
 'transition from a crouched position to a standing position, leaning and hiding behind a wall':'在墙后倚靠隐蔽，从蹲姿过渡到站姿。',
 'person crouches before rising to their feet, as if taking cover behind a wall':'先蹲低，再起身站立，像是在墙后寻找掩护。',
 'A person crouching down initially rises and turns backward to stand upright, facing right.':'从蹲姿起身并向后转，最后面向右侧直立站着。',
 'from position crouch to hiding behind wall standing, hide behind wall, crouching to standing':'从蹲姿过渡到墙后隐蔽站姿；躲在墙后，由蹲到站。',
 'A person initially crouching down rises and turns backward to stand upright, facing right.':'从蹲姿起身并向后转，最后面向右侧直立站着。',
 'A person standing upright lowers their right-facing head and turns the right leg slightly.':'直立站着，头部朝右并低下，右腿略微转动。',
 'start from standing to crouching while supporting with hand':'从站姿开始，用手支撑过渡到蹲姿',
 'transition from standing to crouching behind a wall for cover':'在墙后由站姿过渡到蹲姿，以便隐蔽。',
 'transition from a standing to crouched position for hiding behind a wall':'为躲在墙后，从站姿过渡到蹲姿。',
 'move from standing position to crouching behind a wall for cover':'从站立转为墙后蹲姿，寻求掩护。',
 'A person standing turns over their left shoulder, transitioning to the crouching position and supporting with their right hand.':'从站姿向左肩后方转身，转入蹲姿，用右手支撑。',
 'from position standing to hiding behind wall crouching, hide behind wall, standing to crouching':'从站姿过渡到墙后隐蔽蹲姿；躲在墙后，由站到蹲。',
 'A person standing upright turns over their left shoulder and sits crouching while taking support with their right hand.':'从直立姿态向左肩后方转身，用右手支撑蹲下。',
 'A person is trying to balance while sitting in a crouched position, and their head is turned to the right.':'保持蹲姿并尝试平衡，头部转向右侧。',
 'neutral position turn right':'自然站姿向右转',
 'person is executing a swift turn to the right in place, beginning with their right foot':'右脚先动，原地快速右转。',
 'person standing in one place turns 90 degrees to the right, leading with their right foot':'右脚领动，原地向右转90度。',
 'individual standing in place turns 90 degrees to the right starting from their right foot':'从原地站立开始，右脚起步，向右转90度。',
 'A person standing in an upright posture gently turns to their right and stands still.':'从直立姿态轻缓右转，然后站定。',
 'start right foot, turn right 90 degrees over right shoulder, 2 steps, standing in one place':'右脚起步，向右肩方向右转90度，两步完成，保持原地。',
 'A person standing in an upright posture gently turns to their right.':'从直立姿态轻缓地向右转。',
 'A person is standing upright with their hands swaying slightly.':'直立站着，双手轻微摆动。',
 'neutral position turn back over right shoulder':'自然站姿向右肩后方转身',
 'from a standing position, turn right 180 degrees over the right shoulder':'从站姿向右肩后方右转180度。',
 'standing still, character takes three steps to complete a 180 degrees turn to the right':'从原地站立开始，分三步完成向右转180度。',
 'person takes three steps to perform a 180 degree turn over their right shoulder':'分三步向右肩后方转身180度。',
 'A person is turning behind over their right shoulder at a moderate pace.':'以中等速度向右肩后方转身。',
 'start right foot, turn right 180 degrees over right shoulder, 3 steps, standing in one place':'右脚起步，向右肩后方右转180度，三步完成，保持原地。',
 'A person is standing in an upright posture, facing forward.':'面向前方，直立站着。',
 'A person is standing facing forward in an upright posture.':'面向前方，保持直立站姿。',
 'vault over an obstacle':'撑越障碍物','vault over a small obstacle':'撑越小型障碍物。','vaulting over':'撑越过去。','jump over a 50 cm high box':'越过50厘米高的箱体。',
 'A person quickly vaults over an obstacle and then stands facing forward.':'快速撑越障碍物，然后面向前方站立。',
 'person vaults over a 50 cm high box with their right leg first and hand placed on the box':'手放在箱体上，以右腿先过的方式撑越50厘米高的箱体。',
 'A person quickly vaults over an obstacle using their left hand and then stands facing forward.':'用左手支撑，快速撑越障碍物，然后面向前方站立。',
 'A person is standing, facing forward.':'面向前方站立。',
 'A person is vaulting over an obstacle by balancing the body weight over their left arm.':'以左臂承重保持平衡，撑越障碍物。',
 'A person is standing upright and vaulting over an obstacle by balancing the body weight over their left arm.':'从直立姿态开始，以左臂承重保持平衡并撑越障碍物。',
 'walk backwards':'向后行走','character takes a few steps in reverse':'向后走几步。','person steps backwards':'向后迈步。','character walking backwards':'向后行走。',
 'A person in an upright stance is walking backwards.':'保持直立姿态向后行走。','move backward, walk backward':'向后移动，向后行走。','A person is walking backwards at a normal pace.':'以正常速度向后行走。',
 'start walking back right diagonal':'向右后斜向起步行走',
 'walking backward starting with right foot and moving diagonally with left foot crossing behind':'右脚起步向后行走，斜向移动时左脚向后交叉。',
 'begin by moving right foot, then move backward diagonally crossing with left foot':'先移动右脚，再向后斜向移动，左脚交叉跟进。',
 'person takes a right diagonal step backward':'向右后斜向迈步。',
 'A person starts walking swiftly toward the back right diagonal in a crosswise manner at a moderate pace.':'以交叉步向右后斜向快速起步，步速适中。',
 'start right foot, move backward diagonally, left foot back crossing, walk backward diagonally':'右脚起步，向后斜移，左脚向后交叉，沿后方斜线行走。',
 'A person starts to walk to the back right diagonal by swiftly stepping the right foot toward the right back diagonal.':'右脚迅速迈向右后斜方，开始向右后斜向行走。',
 'A person walks swiftly toward the back right diagonal in a crosswise manner at a moderate pace by taking wide steps.':'以较大交叉步向右后斜向快速行走，步速适中。',
 'stop walking back right diagonal':'向右后斜走后停止',
 'character stopping as they walk diagonally backwards to the right':'向右后斜向行走时停下。',
 'person stops walking, moving backwards diagonally on the right foot':'向后斜向移动，以右脚迈步并停止行走。',
 'stop moving and walk backwards diagonally':'停止移动，并向后斜向行走。（原文表述需要核对。）',
 'A person walks backward diagonally to the right at a steady pace in a crisscross pattern, then comes to a stop and stands.':'匀速以交叉步向右后斜向行走，然后停下站立。',
 'end left foot, move backward diagonally, left foot back crossing, walk backward diagonally':'左脚结束动作，向后斜移，左脚向后交叉，沿后方斜线行走。',
 'A person walks backward diagonally to the right at a steady pace in a crisscross pattern.':'匀速以交叉步向右后斜向行走。',
 'A person comes to a stop from walking and stands.':'从行走中停下，站立。'
};
const $=id=>document.getElementById(id);
const camera={yaw:0,pitch:0.18,distance:340,target:[0,0,95],fov:90,flySpeed:500};
let catalog,data,current,filter='all',frame=0,playing=false,loadVersion=0,last=0,hover=null;
let rmbHeld=false;
const flyKeys=new Set();
const clipCache=new Map(),clipRequests=new Map();
const MAX_PRELOAD_BYTES=512*1024*1024;
let preloadRunning=false,preloadStop=false,clipCacheBytes=0;
const translate={needs_segmentation:'片段过长，未执行重定向',needs_skeleton_review:'骨架映射待适配，未执行重定向',needs_secondary_review:'技术检查异常，保留二审',retarget_failed:'重定向失败，保留二审',possible_ground_penetration:'疑似穿地',bone_length_deviation:'骨长异常',heading_not_validated:'根朝向待人工核对',contacts_not_validated:'脚接触待人工核对',visual_quality_not_validated:'视觉待验收',auxiliary_pose_not_motion_training:'辅助校正姿态，未用于动作训练'};
Object.assign(translate,{processing_heading_unknown:'部分朝向无效，保留未知',processing_multiple_heights:'脚部存在多个稳定高度候选',processing_source_conflict:'来源说明待核对',processing_historical_note:'有历史人工备注'});
const stateName={approved:'人工通过',rejected:'有问题',unsure:'待定',unreviewed:'未验收'};
const canvases=[$('source'),$('target')];
const sub=(a,b)=>a.map((v,i)=>v-b[i]);
const dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
function normal(a){let n=Math.hypot(...a);return a.map(v=>v/n)}
function reviewPage(){return Number(new URLSearchParams(location.search).get('page')||1)}
function pageUrl(page){const url=new URL(location.href);url.searchParams.set('page',page);return url}
async function api(path,options){const url=new URL(path,location.origin);url.searchParams.set('page',reviewPage());const response=await fetch(url,options);const value=await response.json();if(!response.ok)throw Error(value.error||response.status);return value}
function clipKey(entry){return `${entry.id}|${entry.source_sha256||''}|${entry.output_sha256||''}`}
async function loadClip(entry,preloading=false){
 const key=clipKey(entry);
 if(clipCache.has(key))return clipCache.get(key);
 if(clipRequests.has(key))return clipRequests.get(key);
 const request=(async()=>{
  const clip=await api('/api/clip/'+entry.id);
  if(entry.source_sha256&&clip.source_sha256!==entry.source_sha256||clip.target&&entry.output_sha256&&clip.output_sha256!==entry.output_sha256)throw Error('动作版本已变化，请刷新名单');
  let bytes=0;
  for(const rig of [clip.source,clip.target])if(rig){rig.positions=Float32Array.from(rig.positions);bytes+=rig.positions.byteLength;if(rig.root_positions){rig.root_positions=Float32Array.from(rig.root_positions);bytes+=rig.root_positions.byteLength}}
  if(clipCacheBytes+bytes<=MAX_PRELOAD_BYTES){clipCache.set(key,clip);clipCacheBytes+=bytes}
  else if(preloading)throw Error('本页缓存已满，此动作仍可单独加载');
  return clip;
 })();
 clipRequests.set(key,request);
 try{return await request}finally{clipRequests.delete(key)}
}
async function preloadPage(){
 if(!catalog)return;
 if(preloadRunning){preloadStop=true;$('preloadClips').textContent='正在停止…';return}
 const entries=[...catalog.entries];
 if(entries.length>50){$('preloadStatus').textContent='当前名单超过 50 条，请先分页';return}
 preloadRunning=true;preloadStop=false;
 let cursor=0,completed=0,failed=0;
 const report=()=>{$('preloadStatus').textContent=`已加载 ${completed} / ${entries.length}${failed?` · 失败 ${failed} 条，可重试`:''}`};
 $('preloadClips').textContent='停止加载';report();
 async function worker(){
  while(!preloadStop&&cursor<entries.length){const entry=entries[cursor++];try{await loadClip(entry,true);completed++}catch(error){failed++;$('preloadStatus').title=error.message}report()}
 }
 try{await Promise.all([worker(),worker()])}finally{
  preloadRunning=false;$('preloadClips').textContent=failed||preloadStop?'继续加载本页':'一键加载本页';
  $('preloadStatus').textContent+=preloadStop?' · 已停止':!failed?' · 切换时直接使用缓存':'';
 }
}
$('preloadClips').onclick=preloadPage;
function visible(){return catalog.entries.filter(e=>(filter==='all'||e.group===filter)&&e.name.toLowerCase().includes($('search').value.toLowerCase()))}
function list(){const holder=$('list');holder.replaceChildren();for(const entry of visible()){const button=document.createElement('button');button.className='item'+(entry.id===current?.id?' active':'');const title=document.createElement('strong');title.textContent=entry.name;const meta=document.createElement('small');meta.className=entry.group==='issue'?'issue':'';meta.textContent=(entry.group==='issue'?'● 待核对':catalog.native_mode?'◇ 待人工审核':catalog.prepared_mode?'◇ 分类抽样':'◇ 固定 5% 抽样')+' · '+(stateName[catalog.decisions[entry.id]?.status]||'未验收');button.append(title,meta);button.onclick=()=>select(entry);holder.append(button)}$('progress').textContent=`已标记 ${Object.values(catalog.decisions).filter(d=>d.status!=='unreviewed').length} / ${catalog.entries.length} · 记录保存在本机`}
async function select(entry){if(current&&$('note').value!==(catalog.decisions[current.id]?.note||'')&&!confirm('当前备注尚未保存，仍要切换吗？'))return;const version=++loadVersion;current=entry;data=null;playing=false;frame=0;hover=null;$('play').textContent='▶ 播放';$('loading').classList.remove('error');$('loading').style.display='grid';$('loading').textContent='校验来源哈希并加载真实动作…';$('name').textContent=entry.name;$('description').textContent=entry.reason||'';$('group').textContent=catalog.retarget_mode?'UE → SOMA · 来源/目标复核':catalog.prepared_mode?'prepared 数据 · 人工复核':catalog.exception_mode?'异常分流 · 人工筛选':'疑似问题 · 全部人工复核';$('note').value=catalog.decisions[entry.id]?.note||'';$('saved').textContent=stateName[catalog.decisions[entry.id]?.status]||'未验收';$('flags').replaceChildren();for(const flag of [...entry.flags,...entry.integrity_errors]){const tag=document.createElement('span');tag.className='tag warn';tag.textContent=translate[flag]||flag;$('flags').append(tag)}if(!entry.flags.length&&!entry.integrity_errors.length){const tag=document.createElement('span');tag.className='tag';tag.textContent='基础完整性 / 几何初筛未发现问题';$('flags').append(tag)}list();try{const loaded=await loadClip(entry);if(version!==loadVersion)return;data=loaded;$('timeline').max=data.frames-1;$('timeline').value=0;$('sourceCount').textContent=data.source.names.length+' 骨骼';$('targetCount').textContent=data.target?data.target.names.length+' 骨骼':'未生成';document.querySelector('.view-mode-label').textContent=data.target?'导出 Root 坐标 · 查看位移':'源动作 Root 坐标 · 查看位移';$('description').textContent=`${data.package} / ${data.description}${data.review_note?' · '+data.review_note:''}`;$('loading').style.display='none';if(catalog.retarget_mode)focusCurrentFrame();else fitView();updateTime()}catch(error){if(version===loadVersion){$('loading').textContent='无法加载：'+error.message;$('loading').classList.add('error')}}}
function position(rig,f,i){let o=(f*rig.names.length+i)*3;return [rig.positions[o],rig.positions[o+1],rig.positions[o+2]]}
function rootPosition(rig,f){if(data?.processing&&rig===data.source){const o=f*3;return data.processing.root_positions.slice(o,o+3)}if(rig.root_positions){const o=f*3;return [rig.root_positions[o],rig.root_positions[o+1],rig.root_positions[o+2]]}return position(rig,f,rig.pelvis)}
// 始终使用动画导出坐标，保留 Root 的 XYZ 位移与高度。
function origin(){return [0,0,0]}
function basis(){
 const view=$('view').value;
 if(view==='top')return {direction:[0,0,1],forward:[0,0,-1],right:[0,1,0],up:[1,0,0]};
 const yaw=view==='front'?0:view==='back'?Math.PI:view==='side'?-Math.PI/2:camera.yaw;
 const pitch=view==='perspective'?camera.pitch:0;
 const direction=[Math.cos(pitch)*Math.cos(yaw),Math.cos(pitch)*Math.sin(yaw),Math.sin(pitch)];
 // 输入已经是 UE 左手坐标。右手相机基会把角色和场景一起镜像。
 const forward=direction.map(v=>-v),right=normal(cross([0,0,1],forward));
 return {direction,forward,right,up:cross(forward,right)};
}
function projection(width,height){
 const {direction,forward,right,up}=basis(),orthographic=$('view').value!=='perspective';
 const eye=camera.target.map((v,i)=>v+direction[i]*camera.distance);
 const focal=height/(2*Math.tan(camera.fov*Math.PI/360));
 return p=>{
  const rel=sub(p,orthographic?camera.target:eye),z=dot(sub(p,eye),forward);
  if(!orthographic&&z<1)return null;
  const scale=orthographic?height/(2*camera.distance):focal/z;
  return [width/2+dot(rel,right)*scale,height/2-dot(rel,up)*scale,z];
 };
}
function fitView(resetOrientation=true){
 if(resetOrientation){camera.yaw=0;camera.pitch=.18;$('view').value='perspective'}
 if(!data){camera.target=[0,0,95];camera.distance=340;return}
 const points=[];
 for(const rig of [data.source,data.target])if(rig)for(let f=0;f<data.frames;f++){
  for(let i=0;i<rig.names.length;i++)points.push(position(rig,f,i));
  points.push(rootPosition(rig,f));
 }
 const mins=[Infinity,Infinity,Infinity],maxs=[-Infinity,-Infinity,-Infinity];
 for(const p of points)for(let i=0;i<3;i++){mins[i]=Math.min(mins[i],p[i]);maxs[i]=Math.max(maxs[i],p[i])}
 camera.target=mins.map((v,i)=>(v+maxs[i])/2);
 const {direction,right,up}=basis(),rect=canvases[0].getBoundingClientRect();
 const aspect=Math.max(.25,rect.width/Math.max(1,rect.height));
 let required=150;
 for(const p of points){
  const rel=sub(p,camera.target),depth=dot(rel,direction);
  required=Math.max(required,depth+Math.abs(dot(rel,up))/Math.tan(camera.fov*Math.PI/360)*1.15,
                    depth+Math.abs(dot(rel,right))/(aspect*Math.tan(camera.fov*Math.PI/360))*1.15);
 }
 camera.distance=Math.min(100000,required);
}
function focusCurrentFrame(){
 if(!data)return;
 const rig=data.source||data.target,f=Math.floor(frame),offset=origin(rig,f);
 const points=rig.names.map((_,i)=>sub(position(rig,f,i),offset));
 const mins=[Infinity,Infinity,Infinity],maxs=[-Infinity,-Infinity,-Infinity];
 for(const p of points)for(let i=0;i<3;i++){mins[i]=Math.min(mins[i],p[i]);maxs[i]=Math.max(maxs[i],p[i])}
 camera.target=mins.map((v,i)=>(v+maxs[i])/2);
 const {right,up}=basis(),rect=canvases[0].getBoundingClientRect(),aspect=Math.max(.25,rect.width/Math.max(1,rect.height));
 const halfWidth=Math.max(...points.map(p=>Math.abs(dot(sub(p,camera.target),right))));
 const halfHeight=Math.max(...points.map(p=>Math.abs(dot(sub(p,camera.target),up))));
 const extent=Math.max(halfHeight,halfWidth/aspect,50);
 camera.distance=$('view').value==='perspective'?Math.max(150,extent/Math.tan(camera.fov*Math.PI/360)*1.7):Math.max(75,extent*1.3);
}
function draw(canvas,rig,color){const rect=canvas.getBoundingClientRect();if(rect.width<=0||rect.height<=0)return;const dpr=Math.min(devicePixelRatio||1,2);if(canvas.width!==Math.round(rect.width*dpr)||canvas.height!==Math.round(rect.height*dpr)){canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr)}const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);const w=rect.width,h=rect.height;ctx.clearRect(0,0,w,h);const gradient=ctx.createLinearGradient(0,0,0,h);gradient.addColorStop(0,'#0e1724');gradient.addColorStop(1,'#141f2e');ctx.fillStyle=gradient;ctx.fillRect(0,0,w,h);const project=projection(w,h);function line(a,b,c,width=1){a=project(a);b=project(b);if(!a||!b)return;ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.strokeStyle=c;ctx.lineWidth=width;ctx.stroke()}
const step=camera.distance>5000?500:camera.distance>1200?100:25;
const half=Math.max(500,Math.min(30000,camera.distance*1.2)),view=$('view').value;
const gridAxes=view==='front'||view==='back'?[1,2]:view==='side'?[0,2]:[0,1];
const center=gridAxes.map(axis=>Math.round(camera.target[axis]/step)*step);
 const planePoint=(u,v)=>{const p=[0,0,0];p[gridAxes[0]]=u;p[gridAxes[1]]=v;return p};
for(let n=-half;n<=half;n+=step){
 line(planePoint(center[0]+n,center[1]-half),planePoint(center[0]+n,center[1]+half),'#223142');
 line(planePoint(center[0]-half,center[1]+n),planePoint(center[0]+half,center[1]+n),'#223142');
}
line([0,0,0],[50,0,0],'#e98987',2);line([0,0,0],[0,50,0],'#85c292',2);line([0,0,0],[0,0,50],'#86aaf3',2);
ctx.font='10px Segoe UI';ctx.fillStyle='#697e98';ctx.fillText(`网格 ${step} cm · ${gridAxes.map(i=>'XYZ'[i]).join('')} 平面 · Z ↑`,15,h-15);if(!rig){if(canvas===canvases[1]&&current){ctx.textAlign='center';ctx.font='18px Segoe UI';ctx.fillStyle='#f1b865';ctx.fillText('尚未生成 UE 重定向输出',w/2,h/2);ctx.font='13px Segoe UI';ctx.fillStyle='#9babc0';ctx.fillText(current.state==='needs_segmentation'?'片段超过当前帧数上限，需先分段':current.state==='needs_skeleton_review'?'源骨架尚未适配已验证映射':'没有可验证的输出；源数据保留',w/2,h/2+28);ctx.textAlign='start'}return;}
const f=Math.max(0,Math.min(data.frames-1,Math.floor(frame))),offset=origin(rig,f),points=rig.names.map((_,i)=>sub(position(rig,f,i),offset));
if($('trail').checked){let previous;for(let k=0;k<data.frames;k+=Math.max(1,Math.floor(data.frames/300))){const p=sub(rootPosition(rig,k),offset);if(previous)line(previous,p,color+'99',1.5);previous=p}line(previous,sub(rootPosition(rig,data.frames-1),offset),color+'99',1.5);for(const [sample,shade,label] of [[0,'#6dafff','起点'],[data.frames-1,'#f7b267','终点']]){const p=project(sub(rootPosition(rig,sample),offset));if(p){ctx.beginPath();ctx.arc(p[0],p[1],4,0,2*Math.PI);ctx.fillStyle=shade;ctx.fill();ctx.font='10px Segoe UI';ctx.fillText(label,p[0]+6,p[1]-6)}}}
if(data.observation&&rig===data.source)for(const [role,label,shade] of observationRoles){
 if(!$('trace_'+role).checked)continue;
 const joint=data.observation.roles[role];let previous;
 for(let k=0;k<data.frames;k+=Math.max(1,Math.floor(data.frames/300))){const p=position(rig,k,joint);if(previous)line(previous,p,shade+'99',1.4);previous=p}
 line(previous,position(rig,data.frames-1,joint),shade+'99',1.4);
 const p=project(points[joint]);if(p){ctx.fillStyle=shade;ctx.beginPath();ctx.arc(p[0],p[1],5,0,Math.PI*2);ctx.fill();ctx.font='11px Segoe UI';ctx.fillText(label,p[0]+7,p[1]-7)}
}
const edges=[];for(let i=0;i<points.length;i++){let parent=rig.parents[i];if(parent<0)continue;const a=project(points[parent]),b=project(points[i]);if(a&&b)edges.push({i,a,b,z:(a[2]+b[2])/2})}edges.sort((a,b)=>b.z-a.z);for(const e of edges){ctx.beginPath();ctx.moveTo(e.a[0],e.a[1]);ctx.lineTo(e.b[0],e.b[1]);ctx.strokeStyle=color;ctx.lineWidth=/Hand|index|middle|ring|pinky|thumb|twist/i.test(rig.names[e.i])?1.2:2.5;ctx.stroke()}
let nearest=null;for(let i=0;i<points.length;i++){const p=project(points[i]);if(!p)continue;ctx.beginPath();ctx.arc(p[0],p[1],i===rig.pelvis?4:2,0,2*Math.PI);ctx.fillStyle=i===rig.pelvis?'#ffffff':color;ctx.fill();if($('names').checked){ctx.fillStyle='#b1c3d7';ctx.font='8px Segoe UI';ctx.fillText(rig.names[i],p[0]+4,p[1]-3)}if(hover?.canvas===canvas){const dist=Math.hypot(p[0]-hover.x,p[1]-hover.y);if(dist<15&&(!nearest||dist<nearest.dist))nearest={p,dist,i}}}const root=project(sub(rootPosition(rig,f),offset));if(root){ctx.beginPath();ctx.arc(root[0],root[1],5,0,2*Math.PI);ctx.fillStyle='#ff8b69';ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=1;ctx.stroke()}if(nearest){ctx.font='12px Segoe UI';const text=rig.names[nearest.i];ctx.fillStyle='#070c12e8';ctx.fillRect(nearest.p[0]+8,nearest.p[1]-24,ctx.measureText(text).width+16,24);ctx.fillStyle='#fff';ctx.fillText(text,nearest.p[0]+16,nearest.p[1]-8)}}
const observationRoles=[['pelvis','骨盆','#ffce6b'],['left_hand','左手','#ff91b8'],['right_hand','右手','#b59aff'],['left_foot','左脚','#80d36e'],['right_foot','右脚','#71b7ff']];
let observedData=null;
function chineseReading(value,key=''){
 if(key==='content_name'||key==='content_short_description_2')return '开发用名称，保留英文；动作中文说明见上方';
 if(value==='0'||value==='1')return `来源原值 ${value}，具体字段含义待核对`;
 return reviewZh[String(value).trim()]||'暂未提供这条原文的中文释义';
}
function bilingual(value){return `${chineseReading(value)} / ${value}`}
function bilingualDescription(value){return `${chineseReading(value)}\nEnglish: ${value}`}
let processingData=null;
const contactRoles=[['left_foot','左脚'],['right_foot','右脚'],['left_hand','左手'],['right_hand','右手']];
const contactClass=value=>value===1?'contact-positive':value===0?'contact-negative':'contact-unknown';
function updateProcessing(f){
 const processing=data.processing,summary=data.observation?.processing;
 $('processing').hidden=!processing;
 if(!processing)return;
 if(processingData!==data){
  processingData=data;$('contactTimeline').replaceChildren();$('historicalNotes').replaceChildren();
  $('processingSummary').textContent=`Root 代理取骨盆水平位置及有效朝向；全部高度保留在身体中。重合成最大误差 ${summary.recomposition_max_abs_error_m.toExponential(2)} m。当前朝向有效帧占 ${(summary.heading_valid_fraction*100).toFixed(1)}%。`;
  $('processingPolicy').textContent='按来源声明秒数保留原时间轴，不强制改成名义120 FPS。稳定高度是脚踝/脚趾代理点的观察结果，不是已知台阶表面；未知区间不作为确定的接触监督。Root 是离线代理，不是真实 Actor 轨道。';
  for(const [column,[role,label]] of contactRoles.entries()){
   const title=document.createElement('div'),strip=document.createElement('div');strip.className='contact-strip';
   const levels=summary.limbs[role].stable_height_levels_m||[];
   title.textContent=label+(levels.length?' · 稳定代理高度 '+levels.map(v=>(v*100).toFixed(1)+' cm').join(' / '):' · 无已确认支撑面');
   for(let start=0;start<data.frames;){
    const value=processing.contact_states[start][column];let end=start+1;
    while(end<data.frames&&processing.contact_states[end][column]===value)end++;
    const button=document.createElement('button'),first=start;
    button.className=contactClass(value);button.style.flex=String(end-start);
    button.title=`${label} · 播放帧 ${start+1}–${end} · ${{'-1':'未知',0:'非接触候选',1:'接触候选'}[value]}`;
    button.setAttribute('aria-label',button.title);button.onclick=()=>{frame=first;pauseStep(0)};
    strip.append(button);start=end;
   }
   $('contactTimeline').append(title,strip);
  }
  for(const item of summary.historical_notes){const note=document.createElement('p');note.textContent=`历史备注（${item.cohort}，原结论 ${stateName[item.status]||item.status}，不继承）：\n${item.note}`;$('historicalNotes').append(note)}
  if(!summary.historical_notes.length)$('historicalNotes').textContent='此动作没有历史备注。';
 }
 $('contactState').replaceChildren();
 contactRoles.forEach(([role,label],column)=>{
  const value=processing.contact_states[f][column],badge=document.createElement('span');badge.className='tag '+contactClass(value);
  badge.textContent=`${label}：${{'-1':'未知',0:'非接触候选',1:'接触候选'}[value]}${role.includes('hand')&&processing.stable_mask[f][column]?'（低速稳定）':''}`;$('contactState').append(badge);
 });
}
function setupSemantics(observation){
 const reading=observation.semantic_reading;
 if(!reading)return;
 $('semanticSummary').textContent=reading.summary_zh+'（来源文本释义，待视觉复核）';
 $('semanticIssues').replaceChildren();$('semanticEvents').replaceChildren();$('semanticFields').replaceChildren();
 for(const issue of reading.issues){const item=document.createElement('li');item.textContent=issue;$('semanticIssues').append(item)}
 const fieldNames={package:'动作大类',category:'数据分类',content_name:'开发名称',content_natural_desc_1:'自然语言描述 1',content_natural_desc_2:'自然语言描述 2',content_natural_desc_3:'自然语言描述 3',content_natural_desc_4:'自然语言描述 4',content_technical_description:'技术说明',content_short_description:'短说明',content_short_description_2:'开发简称',content_type_of_movement:'运动类型',content_body_position:'身体姿态',content_props:'环境 / 道具字段',content_all_rigplay_styles:'来源风格',content_uniform_style:'统一风格',content_horizontal_move:'水平移动字段（原值）',content_vertical_move:'垂直移动字段（原值）',content_complex_action:'复杂动作字段（原值）',content_repeated_action:'重复动作字段（原值）'};
 for(const [key,value] of Object.entries(observation.labels)){const term=document.createElement('dt'),description=document.createElement('dd');term.textContent=(fieldNames[key]||key)+' · '+key;description.textContent=`${chineseReading(value,key)}\nEnglish / 原值: ${value}`;$('semanticFields').append(term,description)}
 if(reading.temporal){
  for(const event of reading.temporal.events){const button=document.createElement('button');button.textContent=`${event.start_time.toFixed(2)}–${event.end_time.toFixed(2)}s · ${bilingualDescription(event.description)}`;button.disabled=event.start_time>(data.frames-1)/data.fps;button.onclick=()=>{frame=Math.min(data.frames-1,Math.ceil(event.start_time*data.fps));pauseStep(0)};$('semanticEvents').append(button)}
  if(reading.temporal.ends_beyond_motion.length||reading.temporal.gaps_s.length){const note=document.createElement('p');note.textContent='分段说明有未覆盖区间或边界超出当前动作时长；原时间保留，不自动拉伸。';$('semanticEvents').append(note)}
 }
}
function updateObservation(f){
 const observation=data.observation;
 $('observation').hidden=!observation;
 $('semantics').hidden=!observation?.semantic_reading;
 if(!observation)return;
 if(observedData!==data){
  observedData=data;$('observationTrails').replaceChildren();$('phaseCandidates').replaceChildren();
  setupSemantics(observation);
  if(!catalog?.processing_mode)$('queuePill').textContent='● SOMA 观察试点 · 候选待审核';
  const groups={walk:'行走',run:'慢跑',turn:'转身',stand:'站立保持',crouch:'蹲姿动作',supported_transition:'有支撑的姿态过渡',jump:'跳跃',vault:'撑越障碍',climb:'上 / 下箱'};
  $('group').textContent=`观察分组：${groups[observation.group]||observation.group} / ${observation.group} · 来源帧率 ${data.fps.toFixed(6)} FPS`;
  document.querySelector('.view-mode-label').textContent=data.processing?'离线 Root 代理与 Hips 分开观察':'源 Root 与 Hips 分开观察';
  for(const [role,label,color] of observationRoles){const wrapper=document.createElement('label'),box=document.createElement('input');box.type='checkbox';box.id='trace_'+role;box.checked=true;wrapper.style.color=color;wrapper.append(box,document.createTextNode(label+'轨迹'));$('observationTrails').append(wrapper)}
  const labels=observation.labels;
  $('sourceLabels').textContent=`运动：${bilingual(labels.content_type_of_movement)}；姿态：${bilingual(labels.content_body_position)}\n动作：${bilingual(labels.content_short_description)}\n分类：${bilingual(labels.category)}；道具字段：${labels.content_props==='0'?'原值 0（未标注道具） / 0':bilingual(labels.content_props)}\n来源说明：${bilingualDescription(labels.content_natural_desc_1)}`;
  const names={source_start:'源起点',source_end:'源终点',pelvis_height_min:'骨盆最低',pelvis_height_max:'骨盆最高'};
  for(const event of observation.phase_candidates){const button=document.createElement('button');button.textContent=`${names[event.kind]} · 帧 ${event.frame+1}`;button.onclick=()=>{frame=event.frame;pauseStep(0)};$('phaseCandidates').append(button)}
 }
 const active=ranges=>ranges.some(([start,end])=>f>=start&&f<=end);
 const stance=active(observation.stance_candidates.bilateral_knee_bend)?'两侧膝盖都明显弯曲，可能是蹲下、收腿或缓冲':active(observation.stance_candidates.bilateral_knee_straight)?'两条腿都接近伸直；这不等于已经确认站立':'未归入“两腿都伸直”或“两膝都弯曲”，也可能一条腿弯曲';
 const stable=observationRoles.slice(1).filter(([role])=>active(observation.stability_candidates[role].intervals)).map(([,label])=>label);
 const hips=position(data.source,f,observation.roles.pelvis),first=position(data.source,0,observation.roles.pelvis);
 $('observationState').textContent=`腿部观察：${stance}。\n位置变化较小的部位：${stable.join('、')||'暂未发现'}。\n是否踩地或扶住物体：目前无法确认；位置不动也可能悬空。`;
 $('hipsCoordinates').textContent=`骨盆 Hips（显示坐标，cm）：X ${hips[0].toFixed(1)} / Y ${hips[1].toFixed(1)} / Z ${hips[2].toFixed(1)}。相比开头的变化：${sub(hips,first).map(v=>v.toFixed(1)).join(' / ')}。这些是骨骼位置，不是游戏角色 Actor 的位置。`;
 if(observation.semantic_reading){const temporal=observation.semantic_reading.temporal,t=f/data.fps,event=temporal?.events.find(e=>t>=e.start_time&&t<e.end_time);$('semanticCurrent').textContent=`当前片段说明（时间定位待核对）：\n${event?bilingualDescription(event.description):'这段时间没有来源说明，暂时未知。'}`}
}
function updateTime(){if(!data)return;const f=Math.floor(frame);$('timeline').value=f;$('time').textContent=`${f+1} / ${data.frames} · ${(f/data.fps).toFixed(2)}s`;const rig=data.target||data.source,root=rootPosition(rig,f),first=rootPosition(rig,0),delta=sub(root,first),kind=data.processing?'派生 Root 代理':!rig.root_positions?'骨盆参考':catalog?.exception_mode&&!data.target?'源动作 Root（UE 轴）':data.observation?'来源 Root':'导出 Root';$('rootLocation').textContent=`${kind} (cm) X ${root[0].toFixed(1)} · Y ${root[1].toFixed(1)} · Z ${root[2].toFixed(1)} | ΔX ${delta[0].toFixed(1)} · ΔY ${delta[1].toFixed(1)} · ΔZ ${delta[2].toFixed(1)}`;updateObservation(f);updateProcessing(f)}
function tick(now){
 const dt=Math.min(.05,Math.max(0,(now-last)/1000));
 if(rmbHeld&&$('view').value==='perspective'&&flyKeys.size){
  const {forward,right}=basis(),motion=[0,0,0];
  const add=(axis,sign)=>{for(let i=0;i<3;i++)motion[i]+=axis[i]*sign};
  if(flyKeys.has('KeyW')||flyKeys.has('ArrowUp'))add(forward,1);
  if(flyKeys.has('KeyS')||flyKeys.has('ArrowDown'))add(forward,-1);
  if(flyKeys.has('KeyD')||flyKeys.has('ArrowRight'))add(right,1);
  if(flyKeys.has('KeyA')||flyKeys.has('ArrowLeft'))add(right,-1);
  if(flyKeys.has('KeyE')||flyKeys.has('PageUp'))add([0,0,1],1);
  if(flyKeys.has('KeyQ')||flyKeys.has('PageDown'))add([0,0,1],-1);
  if(flyKeys.has('KeyR'))add(basis().up,1);
  if(flyKeys.has('KeyF'))add(basis().up,-1);
  const length=Math.hypot(...motion);
  if(length)camera.target=camera.target.map((v,i)=>v+motion[i]/length*camera.flySpeed*dt);
 }
 if(data&&playing){frame+=dt*data.fps*Number($('speed').value);if(frame>=data.frames){if($('loop').checked)frame%=data.frames;else{frame=data.frames-1;playing=false;$('play').textContent='▶ 播放'}}updateTime()}
 last=now;draw(canvases[0],data?.source,'#5bd6cc');draw(canvases[1],data?.target,'#f1b865');requestAnimationFrame(tick);
}
function pauseStep(delta){playing=false;$('play').textContent='▶ 播放';if(data){frame=Math.max(0,Math.min(data.frames-1,Math.floor(frame)+delta));updateTime()}}
$('play').onclick=()=>{if(!data)return;playing=!playing;$('play').textContent=playing?'❚❚ 暂停':'▶ 播放'};$('prevFrame').onclick=()=>pauseStep(-1);$('nextFrame').onclick=()=>pauseStep(1);$('timeline').oninput=()=>{const selected=Number($('timeline').value);pauseStep(0);frame=selected;updateTime()};
// Epic Viewport Controls：RMB 原地看向、Alt+LMB 绕焦点、MMB 平移、RMB+WASD/EQ 飞行。
function cameraInputMode(e){
 const left=!!(e.buttons&1),right=!!(e.buttons&2),middle=!!(e.buttons&4);
 if($('view').value!=='perspective')return left&&right?'dolly':right||middle?'pan':null;
 if(e.altKey&&right)return 'dolly';
 if(e.altKey&&middle)return 'pan';
 if(e.altKey&&left)return 'orbit';
 if(left&&right||middle)return 'pan';
 if(right)return 'look';
 if(left)return 'drive';
 return null;
}
function panCamera(dx,dy,height){
 const {right,up}=basis();
 const scale=$('view').value==='perspective'?2*camera.distance*Math.tan(camera.fov*Math.PI/360)/height:2*camera.distance/height;
 camera.target=camera.target.map((v,i)=>v-right[i]*dx*scale+up[i]*dy*scale);
}
function rotateCamera(dx,dy,keepEye){
 const eye=keepEye?camera.target.map((v,i)=>v+basis().direction[i]*camera.distance):null;
 camera.yaw+=dx*.008;
 camera.pitch=Math.max(-1.45,Math.min(1.45,camera.pitch+dy*.008));
 if(eye){const {direction}=basis();camera.target=eye.map((v,i)=>v-direction[i]*camera.distance)}
}
for(const canvas of canvases){
 let drag=null;
 canvas.tabIndex=0;
 canvas.oncontextmenu=e=>e.preventDefault();
 canvas.onpointerdown=e=>{
  const mode=cameraInputMode(e);
  if(!mode)return;
  e.preventDefault();canvas.focus();
  rmbHeld=!!(e.buttons&2);
  drag={x:e.clientX,y:e.clientY,id:e.pointerId};
  canvas.setPointerCapture(e.pointerId);canvas.style.cursor='grabbing';
 };
 canvas.onpointermove=e=>{
  const box=canvas.getBoundingClientRect();hover={canvas,x:e.clientX-box.left,y:e.clientY-box.top};
  if(!drag||drag.id!==e.pointerId)return;
  rmbHeld=!!(e.buttons&2);
  const dx=e.clientX-drag.x,dy=e.clientY-drag.y,mode=cameraInputMode(e);
  if(mode==='pan')panCamera(dx,dy,Math.max(1,box.height));
  else if(mode==='dolly')camera.distance=Math.max(40,Math.min(30000,camera.distance*Math.exp(dy*.01)));
  else if(mode==='orbit')rotateCamera(dx,dy,false);
  else if(mode==='look')rotateCamera(dx,dy,true);
  else if(mode==='drive'){
   rotateCamera(dx,0,true);
   const {forward}=basis(),amount=-dy*camera.distance*.008;
   camera.target=camera.target.map((v,i)=>v+forward[i]*amount);
  }
  drag.x=e.clientX;drag.y=e.clientY;
 };
 const finish=e=>{rmbHeld=!!(e.buttons&2);if(drag?.id===e.pointerId&&e.buttons===0){drag=null;canvas.style.cursor='grab'}};
 canvas.onpointerup=finish;canvas.onpointercancel=e=>{rmbHeld=false;drag=null;canvas.style.cursor='grab'};
 canvas.onpointerleave=()=>hover=null;
 canvas.addEventListener('wheel',e=>{
  e.preventDefault();
  if(rmbHeld&&$('view').value==='perspective'){
   camera.flySpeed=Math.max(10,Math.min(10000,camera.flySpeed*Math.exp(-e.deltaY*.001)));
   $('cameraSpeed').textContent=`飞行速度 ${Math.round(camera.flySpeed)} cm/s`;
  }
  else camera.distance=Math.max(40,Math.min(30000,camera.distance*Math.exp(e.deltaY*.001)));
 },{passive:false});
}
$('reset').onclick=fitView;
$('focus').onclick=focusCurrentFrame;
$('view').onchange=()=>{if($('view').value==='perspective'){camera.yaw=-Math.PI/2;camera.pitch=.18}};
$('search').oninput=list;
document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('active',x===b));list()});
let bulkSaving=false;
async function save(status){if(bulkSaving||!current)return;const entry=current, note=$('note').value;$('saved').textContent='保存中…';try{await api('/api/review',{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':catalog.token},body:JSON.stringify({id:entry.id,status,note})});catalog.decisions[entry.id]={status,note};if(current.id===entry.id)$('saved').textContent=stateName[status]+' · 已保存';list()}catch(e){$('saved').textContent='保存失败：'+e.message}}
async function bulkReview(status){
 if(!catalog||bulkSaving)return;
 const action=status==='approved'?'通过':'不通过';
 if(current&&$('note').value!==(catalog.decisions[current.id]?.note||'')){alert(`请先保存当前备注，再批量标记${action}。`);return}
 let done=0;
 bulkSaving=true;$('approveVisible').disabled=true;$('rejectVisible').disabled=true;
 try{
  // 先刷新持久化结论，避免覆盖其他页面已保存的有问题/待定记录。
  const fresh=await api('/api/catalog');catalog.decisions=fresh.decisions;catalog.token=fresh.token;
  const entries=visible().filter(e=>!catalog.decisions[e.id]||catalog.decisions[e.id].status==='unreviewed');
  if(!entries.length){$('bulkResult').textContent='当前列表没有未验收动作。';return}
  const issues=entries.filter(e=>e.group==='issue').length;
  if(!confirm(`将当前筛选中的 ${entries.length} 条未验收动作批量标记${action}（含 ${issues} 条疑似问题）。\n这不代表逐条观看；已有结论保留，不会自动入库或删除。确定继续？`))return;
  for(const entry of entries){
   const previous=catalog.decisions[entry.id]?.note||'';
   const note=previous+`\n[批量${action}：用户一键标记，未声明逐条观看]`;
   await api('/api/review',{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':catalog.token},body:JSON.stringify({id:entry.id,status,note})});
   catalog.decisions[entry.id]={status,note};done++;
   $('bulkResult').textContent=`已保存 ${done} / ${entries.length} 条`;
  }
  $('bulkResult').textContent=`批量${action} ${done} 条，记录已保存；未入库、未删除。`;
 }catch(error){$('bulkResult').textContent=`已保存 ${done} 条，操作停止：${error.message}。可刷新核对后重试。`}
 finally{bulkSaving=false;$('approveVisible').disabled=false;$('rejectVisible').disabled=false;list();if(current){$('note').value=catalog.decisions[current.id]?.note||'';$('saved').textContent=stateName[catalog.decisions[current.id]?.status]||'未验收'}}
}
$('approveVisible').onclick=()=>bulkReview('approved');
$('rejectVisible').onclick=()=>bulkReview('rejected');
document.querySelectorAll('[data-status]').forEach(b=>b.onclick=()=>save(b.dataset.status));$('saveNote').onclick=()=>save(catalog.decisions[current?.id]?.status||'unreviewed');$('nextClip').onclick=()=>{const entries=visible(),index=entries.findIndex(e=>e.id===current?.id);if(entries.length)select(entries[(index+1)%entries.length])};$('export').onclick=async()=>{const result=await api('/api/export');const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='motion-review.json';a.click();URL.revokeObjectURL(url)};
const flightCodes=new Set(['KeyW','KeyA','KeyS','KeyD','KeyQ','KeyE','KeyR','KeyF','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','PageUp','PageDown']);
document.addEventListener('keydown',e=>{
 if(['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName))return;
 if(rmbHeld&&$('view').value==='perspective'&&flightCodes.has(e.code)){e.preventDefault();flyKeys.add(e.code);return}
 if(e.code==='KeyF'&&canvases.includes(document.activeElement)){e.preventDefault();focusCurrentFrame();return}
 if(e.code==='Space'){e.preventDefault();$('play').click()}
 if(e.code==='ArrowLeft'){e.preventDefault();pauseStep(-1)}
 if(e.code==='ArrowRight'){e.preventDefault();pauseStep(1)}
});
document.addEventListener('keyup',e=>flyKeys.delete(e.code));
window.addEventListener('blur',()=>{rmbHeld=false;flyKeys.clear()});
api('/api/catalog').then(value=>{
 catalog=value;
 if(value.native_mode){
  if(!value.retarget_mode)document.body.classList.add('prepared-mode');
  document.querySelector('.brand small').textContent='MotionWeaver / 本地动作数据审核';
  $('queuePill').textContent='● 全量动画审核 · 原始数据受保护';
  $('sampleFilter').hidden=true;
  document.querySelector('[data-filter="issue"]').hidden=true;
  $('search').placeholder='搜索本页动作名称';
  $('sourceLabel').textContent='标准化动画 · 来源骨架';
  $('source').setAttribute('aria-label','标准化动画骨骼交互视图');
  $('disclaimer').textContent='当前显示标准化数据的来源骨骼线框，保留 Root 位移，尚未执行跨骨架映射。画面以厘米显示；蒙皮、接触和碰撞效果需要另行检查。';
  $('saveLocation').textContent=`审核结论保存到 ${value.review_directory}；原始与处理数据不会因标记而改变。`;
  $('export').textContent='导出本页审核记录 ↗';
  document.querySelector('.sync').textContent='单动作骨架 / 时间轴';
 }
 if(value.retarget_mode){
  $('queuePill').textContent='● UE → SOMA · 来源与目标同步对照';
  $('sourceLabel').textContent='来源骨架 · 显示朝向与比例对齐';
  $('targetLabel').textContent='SOMA uniform · 固定参考骨架';
  $('source').setAttribute('aria-label','UE 来源骨骼交互视图');
  $('target').setAttribute('aria-label','SOMA 目标骨骼交互视图');
  $('disclaimer').textContent='来源轨道保留原骨架，右侧为固定 SOMA 骨长的离线重定向。BVH 与原生格式已进行数值回读；蒙皮、真实接触和训练效果仍待验证。';
  document.querySelector('.sync').textContent='来源 / SOMA 同步播放';
 }
 if(value.processing_mode){
  document.body.classList.add('processing-mode');
  document.querySelector('.view-mode-label').textContent='离线 Root 代理与 Hips 分开观察';
  document.querySelector('[data-filter="issue"]').hidden=false;
  document.querySelector('[data-filter="issue"]').textContent='待核对';
  $('queuePill').textContent='● Root / 接触候选试点 · 来源粗语义';
  $('note').placeholder='可备注明显异常或需要修订的来源说明；无需逐帧标注。';
  $('saveLocation').textContent=`本批次抽检记录保存在 ${value.review_directory}；历史备注单独展示，不继承旧批次结论。`;
 }
 if(value.exception_mode)document.querySelector('.view-mode-label').textContent='源动作 Root 坐标 · 查看位移';
 const issues=value.entries.filter(e=>e.group==='issue').length;
 $('total').textContent=value.native_mode||value.exception_mode||value.prepared_mode?`${value.total}（本页 ${value.entries.length}）`:value.entries.length;
 $('summary').textContent=value.native_mode?`全量 ${value.total} 条 · 第 ${value.page} / ${value.page_count} 页 · 每页最多 50 条`:value.prepared_mode?`已审计 ${value.checked} 条 · 复核 ${value.total} 条 · 第 ${value.page} / ${value.page_count} 页`:value.exception_mode?`异常分流 ${value.total} 条 · 第 ${value.page} / ${value.page_count} 页 · 不会自动入库或删除`:`已初筛 ${value.checked} 条 · 疑似问题 ${issues} 条 + 5% 抽样 ${value.entries.length-issues} 条`;
 if(value.prepared_mode){
  document.body.classList.add('prepared-mode');
  $('queuePill').textContent='● prepared 数据复核 · 原始数据受保护';
  $('sampleFilter').textContent='分类抽样';
  $('sourceLabel').textContent='UE 导出动作 · Root 合成';
  $('source').setAttribute('aria-label','UE 导出动作骨骼交互视图');
  $('description').textContent='Root 驱动的 UE 导出动作骨架';
  $('disclaimer').textContent='30 FPS，UE 厘米制动画导出 Root 轨道，始终保留导出位置与高度。它不是关卡 Actor 的世界 Location；蒙皮、接触、碰撞和场景语义仍须在 UE 验收。';
  $('saveLocation').textContent='结论保存到本地 MotionDataLibrary/reviews/decisions.sqlite3；仅用于复核，不会自动修改训练数据。';
  document.querySelector('.sync').textContent='单动作骨架 / 时间轴';
 }
 $('prevPage').disabled=value.page<=1;
 $('nextPage').disabled=value.page>=value.page_count;
 $('prevPage').onclick=()=>location.href=pageUrl(value.page-1);
 $('nextPage').onclick=()=>location.href=pageUrl(value.page+1);
 list();
 if(value.entries.length)select(value.entries[0]);else $('loading').textContent='本次名单为空';
}).catch(e=>$('loading').textContent=e.message);
requestAnimationFrame(tick);
