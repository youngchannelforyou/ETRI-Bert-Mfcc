import argparse
import torch
import logging
import os
import json
from utils import set_seed, get_optimizer_and_scheduler
from dataset import ALL_DICT, get_data_loader
from model import MultimodalTransformer
from tqdm import tqdm
from eval import evaluate

def train(args,
          model,
          trn_loader,
          optimizer,
          scheduler):

    trn_loss, logging_loss = 0, 0
    loss_fct = torch.nn.CrossEntropyLoss()
    iterator = tqdm(enumerate(trn_loader), desc='steps', total=len(trn_loader))

    # start steps
    for step, batch in iterator:
        model.train()
        model.zero_grad()

        # unpack and set inputs
        batch = map(lambda x: x.to(args.device) if x is not None else x, batch)
        audios, a_mask, texts, t_mask, labels = batch
        labels = labels.squeeze(-1).long()

        # feed to model and get loss
        logit, hidden = model(audios, texts, a_mask, t_mask)
        loss = loss_fct(logit, labels.view(-1))
        trn_loss += loss.item()

        # update the model
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip)
        optimizer.step()
        scheduler.step()
        args.global_step += 1

        # summary
        if args.global_step % args.logging_steps == 0:
            cur_logging_loss = (trn_loss - logging_loss) / args.logging_steps
            logging.info("train loss: {:.4f}".format(cur_logging_loss))
            logging_loss = trn_loss


def main(args, sess, num_class):
    set_seed(args.seed)
    
    LABEL_DICT = ALL_DICT[num_class[0]]

    # load data
    loaders = (get_data_loader(
        args=args,
        data_path=args.data_path,
        bert_path=args.bert_path,
        batch_size=args.batch_size,
        num_workers=args.num_workers,
        num_class = num_class,
        split=split
    ) for split in ['train_'+f'{sess:02}', 'dev_'+f'{sess:02}'])
    
    trn_loader, dev_loader = loaders
    
    # initialize model
    model = MultimodalTransformer(
        n_layers=args.n_layers,
        n_heads=args.n_heads,
        n_classes=num_class[1],#찬영
        only_audio=args.only_audio,
        only_text=args.only_text,
        d_audio_orig=args.n_mfcc,
        d_text_orig=768,    # BERT hidden size
        d_model=args.d_model,
        attn_dropout=args.attn_dropout,
        relu_dropout=args.relu_dropout,
        emb_dropout=args.emb_dropout,
        res_dropout=args.res_dropout,
        out_dropout=args.out_dropout,
        attn_mask=args.attn_mask
    ).to(args.device)

    # warmup scheduling
    args.total_steps = round(len(trn_loader) * args.epochs)
    args.warmup_steps = round(args.total_steps * args.warmup_percent)

    # optimizer & scheduler
    optimizer, scheduler = get_optimizer_and_scheduler(args, model)

    logging.info('training starts')
    model.zero_grad()
    args.global_step = 0
    
    for epoch in tqdm(range(1, args.epochs + 1), desc='epochs'):
        test_name = "Session" + str(sess) + "_epoch:" + str(epoch) + "_"
        # training and evaluation steps
        train(args, model, trn_loader, optimizer, scheduler)
        loss, f1 = evaluate(model, dev_loader, args.device, test_name, args.save_path, LABEL_DICT)

        # save model
        model_name = "epoch{}-loss{:.4f}-f1{:.4f}.".format(epoch, loss, f1)
        model_path = os.path.join(args.save_path, model_name)
    torch.save(model.state_dict() , model_path + "Session" + str(sess) + '.pt')
    
    logging.info('training ended')


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    # settings
    parser.add_argument('--only_audio', action='store_true')
    parser.add_argument('--only_text', action='store_true')
    parser.add_argument('--data_path', type=str, default='./data/pkls')
    parser.add_argument('--bert_path', type=str, default='./KoBERT')
    parser.add_argument('--save_path', type=str, default='./result') # 현재 코드 실행시 결과 파일이 교체됨 확인 후 실행 바람
    parser.add_argument('--n_classes', type=int, default=7)
    parser.add_argument('--logging_steps', type=int, default=1)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--num_workers', type=int, default=20)
    parser.add_argument('--cuda', default='cuda')

    # dropouts
    parser.add_argument('--attn_dropout', type=float, default=.3)
    parser.add_argument('--relu_dropout', type=float, default=.3)
    parser.add_argument('--emb_dropout', type=float, default=.3)
    parser.add_argument('--res_dropout', type=float, default=.3)
    parser.add_argument('--out_dropout', type=float, default=.3)

    # architecture
    parser.add_argument('--n_layers', type=int, default=2)
    parser.add_argument('--d_model', type=int, default=40)
    parser.add_argument('--n_heads', type=int, default=2)
    parser.add_argument('--attn_mask', action='store_false')

    # training
    parser.add_argument('--lr', type=float, default=2e-5)
    parser.add_argument('--epochs', type=int, default=5)
    parser.add_argument('--batch_size', type=int, default=128)
    parser.add_argument('--clip', type=float, default=.8)
    parser.add_argument('--warmup_percent', type=float, default=.1)

    # data processing
    parser.add_argument('--max_len_audio', type=int, default=400)
    parser.add_argument('--sample_rate', type=int, default=48000)
    parser.add_argument('--resample_rate', type=int, default=16000)
    parser.add_argument('--n_fft_size', type=int, default=600)
    parser.add_argument('--n_mfcc', type=int, default=40)

    args_ = parser.parse_args()

    # -------------------------------------------------------------- #

    # check usage of modality
    if args_.only_audio and args_.only_text:
        raise ValueError("Please check your usage of modalities.")

    # save config
    with open(os.path.join(args_.save_path, 'config.json'), 'w') as fp:
        json.dump(args_.__dict__, fp, indent=4)

    # seed and device setting
    set_seed(args_.seed)
    os.environ["CUDA_VISIBLE_DEVICES"] = "0,1,2"
    os.environ['CUDA_LAUNCH_BLOCKING'] = "1"

    device = torch.device('cuda:1' if torch.cuda.is_available() else 'cpu')
    args_.device = device

    # log setting
    logger = logging.getLogger(__name__)
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
        level=logging.INFO
    )
    NUM_CLASS_DICT = { 1: ["LABELDICT_B", 3], #LABELDICT_B = neutral, happy, surprise
                   2: ["LABELDICT_B", 3],  #LABELDICT_B = neutral, happy, surprise
                   3: ["LABELDICT_C", 6],  #LABELDICT_C = neutral, happy, angry, surprise, disqust, sad
                   4: ["LABELDICT_C", 6],  #LABELDICT_C = neutral, happy, angry, surprise, disqust, sad
                   5: ["LABELDICT_D", 7],  #LABELDICT_D = neutral, happy, angry, surprise, disqust, sad, fear
                   6: ["LABELDICT_D", 7],  #LABELDICT_D = neutral, happy, angry, surprise, disqust, sad, fear
                   7: ["LABELDICT_E", 5],  #LABELDICT_E = neutral, happy, angry, surprise, disqust
                   8: ["LABELDICT_C", 6],  #LABELDICT_C = neutral, happy, angry, surprise, disqust, sad
                   9: ["LABELDICT_C", 6],  #LABELDICT_C = neutral, happy, angry, surprise, disqust, sad
                   10: ["LABELDICT_D", 7],  #LABELDICT_D = neutral, happy, angry, surprise, disqust, sad, fear
                   11: ["LABELDICT_F", 5], #LABELDICT_F = neutral, happy, surprise, sad, fear
                   12: ["LABELDICT_G", 6], #LABELDICT_G = neutral, happy, angry, surprise, sad, fear
                   13: ["LABELDICT_D", 7], #LABELDICT_D = neutral, happy, angry, surprise, disqust, sad, fear
                   14: ["LABELDICT_H", 6], #LABELDICT_H = neutral, happy, angry, disqust, sad, fear
                   15: ["LABELDICT_D", 7], #LABELDICT_D = neutral, happy, angry, surprise, disqust, sad, fear
                   16: ["LABELDICT_D", 7], #LABELDICT_D = neutral, happy, angry, surprise, disqust, sad, fear
                   17: ["LABELDICT_I", 5], #LABELDICT_I = neutral, happy, angry, surprise, sad
                   18: ["LABELDICT_Q", 5], #LABELDICT_Q" : ['disqust', 'fear', 'happy', 'neutral', 'sad']
                   19: ["LABELDICT_C", 6], #LABELDICT_C = neutral, happy, angry, surprise, disqust, sad
                   20: ["LABELDICT_I", 5], #LABELDICT_I = neutral, happy, angry, surprise, sad
                   21: ["LABELDICT_D", 7], #LABELDICT_D = neutral, happy, angry, surprise, disqust, sad, fear
                   22: ["LABELDICT_C", 6], #LABELDICT_C = neutral, happy, angry, surprise, disqust, sad
                   23: ["LABELDICT_E", 5], #LABELDICT_E = neutral, happy, angry, surprise, disqust
                   24: ["LABELDICT_D", 7], #LABELDICT_D = neutral, happy, angry, surprise, disqust, sad, fear
                   25: ["LABELDICT_D", 7], #LABELDICT_D = neutral, happy, angry, surprise, disqust, sad, fear
                   26: ["LABELDICT_G", 6], #LABELDICT_G = neutral, happy, angry, surprise, sad, fear
                   27: ["LABELDICT_C", 6], #LABELDICT_C = neutral, happy, angry, surprise, disqust, sad
                   28: ["LABELDICT_M", 5], #LABELDICT_M = neutral, happy, angry, surprise, fear
                   29: ["LABELDICT_D", 7], #LABELDICT_D = neutral, happy, angry, surprise, disqust, sad, fear
                   30: ["LABELDICT_C", 6], #LABELDICT_C = neutral, happy, angry, surprise, disqust, sad
                   31: ["LABELDICT_B", 3], #LABELDICT_B = neutral, happy, surprise
                   32: ["LABELDICT_L", 4], #LABELDICT_L = neutral, happy, surprise, disqust
                   33: ["LABELDICT_N", 5], #LABELDICT_N = neutral, happy, surprise, disqust, fear
                   34: ["LABELDICT_M", 5], #LABELDICT_M = neutral, happy, angry, surprise, fear
                   35: ["LABELDICT_E", 5], #LABELDICT_E = neutral, happy, angry, surprise, disqust
                   36: ["LABELDICT_I", 5], #LABELDICT_I = neutral, happy, angry, surprise, sad
                   37: ["LABELDICT_R", 5], #LABELDICT_R: ('disqust', 'happy', 'neutral', 'sad', 'surprise')
                   38: ["LABELDICT_P", 4], #LABELDICT_B = neutral, happy, surprise, sad
                   39: ["LABELDICT_C", 6], #LABELDICT_C = neutral, happy, angry, surprise, disqust, sad
                   40: ["LABELDICT_D", 7], #LABELDICT_D = neutral, happy, angry, surprise, disqust, sad, fear
                  }

    for sess in range(1,41):
        num_class = NUM_CLASS_DICT[sess]
        main(args_,sess,num_class)
        
    #LABELDICT_A = neutral, happy, angry, surprise
    #LABELDICT_B = neutral, happy, surprise
    #LABELDICT_C = neutral, happy, angry, surprise, disqust, sad
    #LABELDICT_D = neutral, happy, angry, surprise, disqust, sad, fear
    #LABELDICT_E = neutral, happy, angry, surprise, disqust
    #LABELDICT_F = neutral, happy, surprise, sad, fear
    #LABELDICT_G = neutral, happy, angry, surprise, sad, fear
    #LABELDICT_H = neutral, happy, angry, disqust, sad, fear
    #LABELDICT_I = neutral, happy, angry, surprise, sad
    #LABELDICT_J = neutral, happy, surprise, disqust, sad, fear
    #LABELDICT_K = neutral, happy, angry, surprise, disqust, fear
    #LABELDICT_L = neutral, happy, surprise, disqust
    #LABELDICT_N = neutral, happy, surprise, disqust, fear
    #LABELDICT_M = neutral, happy, angry, surprise, fear
    #


# python trainch.py \
#   --data_path='./data' \
#   --bert_path='./KoBERT' \
#   --save_path='./practice' \
#   --attn_dropout=.2 \
#   --relu_dropout=.1 \
#   --emb_dropout=.2 \
#   --res_dropout=.1 \
#   --out_dropout=.1 \
#   --n_layers=2 \
#   --d_model=64 \
#   --n_heads=8 \
#   --lr=1e-5 \
#   --epochs=10 \
#   --batch_size=64 \
#   --clip=1.0 \
#   --warmup_percent=.1 \
#   --max_len_audio=400 \
#   --sample_rate=48000 \
#   --resample_rate=16000 \
#   --n_fft_size=200 \
#   --n_mfcc=80


# LABELDICT_A = {
#     "fear": 0,
#     "neutral": 1,
#     "surprise": 2,
#     "angry": 3,
#     "sad": 4,
#     "disqust": 5,
#     "happy": 6,
# }

# ['neutral' 'surprise' 'happy']
# ['neutral' 'happy' 'surprise']
# ['happy' 'neutral' 'disqust' 'angry' 'surprise' 'sad']
# ['neutral' 'sad' 'angry' 'happy' 'surprise' 'disqust']
# ['sad' 'neutral' 'disqust' 'angry' 'happy' 'surprise' 'fear']
# ['neutral' 'happy' 'surprise' 'fear' 'sad']
# ['neutral' 'happy' 'disqust' 'surprise' 'angry']
# ['neutral' 'happy' 'angry' 'sad' 'surprise' 'disqust']
# ['neutral' 'happy' 'angry' 'surprise' 'sad' 'disqust']
# ['neutral' 'sad' 'angry' 'happy' 'disqust' 'surprise' 'fear']
# ['neutral' 'happy' 'surprise' 'sad']
# ['neutral' 'sad' 'happy' 'fear' 'angry' 'surprise']
# ['neutral' 'disqust' 'sad' 'happy' 'fear' 'surprise' 'angry']
# ['neutral' 'sad' 'disqust' 'happy' 'angry' 'fear']
# ['neutral' 'happy' 'sad' 'disqust' 'surprise' 'angry' 'fear']
# ['neutral' 'happy' 'fear' 'surprise' 'angry' 'sad' 'disqust']
# ['neutral' 'happy' 'sad' 'surprise' 'angry']
# ['neutral' 'happy' 'fear' 'disqust' 'sad']
# ['neutral' 'happy' 'surprise' 'angry' 'sad']
# ['neutral' 'angry' 'happy' 'sad' 'surprise']
# ['angry' 'neutral' 'surprise' 'disqust' 'happy' 'sad' 'fear']
# ['happy' 'neutral' 'surprise' 'angry' 'sad' 'disqust']
# ['neutral' 'happy' 'angry' 'surprise' 'disqust']
# ['fear' 'neutral' 'angry' 'happy' 'surprise' 'sad' 'disqust']
# ['neutral' 'happy' 'fear' 'angry' 'surprise' 'disqust']
# ['neutral' 'happy' 'angry' 'fear' 'surprise' 'sad']
# ['neutral' 'sad' 'happy' 'surprise' 'angry' 'disqust']
# ['neutral' 'happy' 'surprise' 'angry' 'fear']
# ['neutral' 'happy' 'angry' 'surprise' 'disqust' 'sad']
# ['neutral' 'happy' 'surprise' 'sad' 'disqust' 'angry']
# ['neutral' 'surprise' 'happy']
# ['neutral' 'surprise' 'happy' 'disqust']
# ['neutral' 'happy' 'surprise' 'disqust' 'fear']
# ['neutral' 'surprise' 'happy' 'angry' 'fear']
# ['neutral' 'happy' 'angry' 'disqust' 'surprise']
# ['happy' 'neutral' 'surprise' 'angry']
# ['neutral' 'happy' 'surprise' 'sad']
# ['neutral' 'happy' 'surprise' 'sad']
# ['neutral' 'happy' 'surprise' 'angry' 'disqust' 'sad']
# ['neutral' 'happy' 'fear' 'sad' 'angry' 'surprise' 'disqust']