# Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import os
import math

from paddle.fluid.dygraph.base import to_variable
import numpy as np
import paddle.fluid as fluid
from paddle.fluid.dygraph.parallel import ParallelEnv
from paddle.fluid.io import DataLoader
from paddle.fluid.dataloader import BatchSampler

from datasets import OpticDiscSeg, Cityscapes
import transforms as T
import models
import utils.logging as logging
from utils import get_environ_info
from utils import ConfusionMatrix
from utils import Timer, calculate_eta


def parse_args():
    parser = argparse.ArgumentParser(description='Model evaluation')

    # params of model
    parser.add_argument(
        '--model_name',
        dest='model_name',
        help="Model type for evaluation, which is one of ('UNet')",
        type=str,
        default='UNet')

    # params of dataset
    parser.add_argument(
        '--dataset',
        dest='dataset',
        help=
        "The dataset you want to evaluation, which is one of ('OpticDiscSeg', 'Cityscapes')",
        type=str,
        default='OpticDiscSeg')

    # params of evaluate
    parser.add_argument(
        "--input_size",
        dest="input_size",
        help="The image size for net inputs.",
        nargs=2,
        default=[512, 512],
        type=int)
    parser.add_argument(
        '--batch_size',
        dest='batch_size',
        help='Mini batch size',
        type=int,
        default=2)
    parser.add_argument(
        '--model_dir',
        dest='model_dir',
        help='The path of model for evaluation',
        type=str,
        default=None)

    return parser.parse_args()


def evaluate(model,
             eval_dataset=None,
             places=None,
             model_dir=None,
             num_classes=None,
             batch_size=2,
             ignore_index=255,
             epoch_id=None):
    ckpt_path = os.path.join(model_dir, 'model')
    para_state_dict, opti_state_dict = fluid.load_dygraph(ckpt_path)
    model.set_dict(para_state_dict)
    model.eval()

    batch_sampler = BatchSampler(
        eval_dataset, batch_size=batch_size, shuffle=False, drop_last=False)
    loader = DataLoader(
        eval_dataset,
        batch_sampler=batch_sampler,
        places=places,
        return_list=True,
    )
    total_steps = len(batch_sampler)
    conf_mat = ConfusionMatrix(num_classes, streaming=True)

    logging.info(
        "Start to evaluating(total_samples={}, total_steps={})...".format(
            len(eval_dataset), total_steps))
    timer = Timer()
    timer.start()
    for step, data in enumerate(loader):
        images = data[0]
        labels = data[1].astype('int64')
        pred, _ = model(images, mode='eval')

        pred = pred.numpy()
        labels = labels.numpy()
        mask = labels != ignore_index
        conf_mat.calculate(pred=pred, label=labels, ignore=mask)
        _, iou = conf_mat.mean_iou()

        time_step = timer.elapsed_time()
        remain_step = total_steps - step - 1
        logging.info(
            "[EVAL] Epoch={}, Step={}/{}, iou={:4f}, sec/step={:.4f} | ETA {}".
            format(epoch_id, step + 1, total_steps, iou, time_step,
                   calculate_eta(remain_step, time_step)))
        timer.restart()

    category_iou, miou = conf_mat.mean_iou()
    category_acc, macc = conf_mat.accuracy()
    logging.info("[EVAL] #image={} acc={:.4f} IoU={:.4f}".format(
        len(eval_dataset), macc, miou))
    logging.info("[EVAL] Category IoU: " + str(category_iou))
    logging.info("[EVAL] Category Acc: " + str(category_acc))
    logging.info("[EVAL] Kappa:{:.4f} ".format(conf_mat.kappa()))
    return miou, macc


def main(args):
    env_info = get_environ_info()
    places = fluid.CUDAPlace(ParallelEnv().dev_id) \
        if env_info['place'] == 'cuda' and fluid.is_compiled_with_cuda() \
        else fluid.CPUPlace()

    if args.dataset.lower() == 'opticdiscseg':
        dataset = OpticDiscSeg
    elif args.dataset.lower() == 'cityscapes':
        dataset = Cityscapes
    else:
        raise Exception(
            "The --dataset set wrong. It should be one of ('OpticDiscSeg', 'Cityscapes')"
        )

    with fluid.dygraph.guard(places):
        eval_transforms = T.Compose([T.Resize(args.input_size), T.Normalize()])
        eval_dataset = dataset(transforms=eval_transforms, mode='eval')

        if args.model_name == 'UNet':
            model = models.UNet(num_classes=eval_dataset.num_classes)

        evaluate(
            model,
            eval_dataset,
            places=places,
            model_dir=args.model_dir,
            num_classes=eval_dataset.num_classes,
            batch_size=args.batch_size)


if __name__ == '__main__':
    args = parse_args()
    main(args)
